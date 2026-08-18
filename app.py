import os
import json
import urllib.request
import subprocess
import shutil
import glob
from datetime import datetime
from flask import Flask, render_template, redirect, flash, request

app = Flask(__name__)
app.secret_key = "greystone-mc-secret" 

# --- CORE DIRECTORIES ---
DATA_DIR = os.environ.get('SNAP_DATA', os.path.abspath('mc_data'))
SERVER_JAR = os.path.join(DATA_DIR, "server.jar")
INSTANCES_DIR = os.path.join(DATA_DIR, "instances")
ACTIVE_WORLD_FILE = os.path.join(DATA_DIR, "active_world.txt")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(INSTANCES_DIR, exist_ok=True)

mc_process = None

# --- STATE HELPERS ---
def get_active_world():
    """Reads the currently selected world from disk."""
    if os.path.exists(ACTIVE_WORLD_FILE):
        with open(ACTIVE_WORLD_FILE, 'r') as f:
            world = f.read().strip()
            if world: return world
            
    # Default world if no world exists yet
    default_world = "My_First_World"
    set_active_world(default_world)
    return default_world

def set_active_world(world_name):
    """Saves the currently selected world to disk."""
    with open(ACTIVE_WORLD_FILE, 'w') as f:
        f.write(world_name.strip())

def get_all_worlds():
    """Returns a list of all world folders in the instances directory."""
    return [d for d in os.listdir(INSTANCES_DIR) if os.path.isdir(os.path.join(INSTANCES_DIR, d))]

def get_paths():
    """Dynamically generates all file paths based on the active world."""
    active = get_active_world()
    base = os.path.join(INSTANCES_DIR, active)
    
    # Ensure instance directories exist
    os.makedirs(base, exist_ok=True)
    os.makedirs(os.path.join(base, "backups"), exist_ok=True)
    
    return {
        "cwd": base,
        "world": os.path.join(base, "world"),
        "backups": os.path.join(base, "backups"),
        "log": os.path.join(base, "server.log"),
        "properties": os.path.join(base, "server.properties"),
        "eula": os.path.join(base, "eula.txt"),
        "name": active
    }

# --- SYSTEM HELPERS ---
def get_latest_jar_url():
    manifest_url = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
    req = urllib.request.urlopen(manifest_url)
    manifest = json.loads(req.read())
    latest_id = manifest["latest"]["release"]
    version_url = next(v["url"] for v in manifest["versions"] if v["id"] == latest_id)
    req = urllib.request.urlopen(version_url)
    version_data = json.loads(req.read())
    return version_data["downloads"]["server"]["url"]

def get_java_path():
    if "SNAP" in os.environ:
        search_path = os.path.join(os.environ["SNAP"], "usr", "lib", "jvm", "java-*-openjdk-*", "bin", "java")
        matches = glob.glob(search_path)
        if matches:
            return matches[0]
    return "java"

# --- ROUTES ---
@app.route("/")
def index():
    paths = get_paths()
    server_running = mc_process is not None and mc_process.poll() is None
    jar_exists = os.path.exists(SERVER_JAR)
    backups = os.listdir(paths["backups"]) if os.path.exists(paths["backups"]) else []
    
    return render_template(
        "index.html", 
        running=server_running, 
        jar_exists=jar_exists, 
        backups=backups,
        active_world=paths["name"],
        all_worlds=get_all_worlds()
    )

@app.route("/switch_world", methods=["POST"])
def switch_world():
    global mc_process
    if mc_process is not None and mc_process.poll() is None:
        flash("Cannot switch worlds while the server is running! Stop it first.", "negative")
        return redirect("/")
        
    new_world = request.form.get("world_name")
    if new_world:
        # Sanitize world folder name
        safe_name = "".join([c for c in new_world if c.isalpha() or c.isdigit() or c in (' ', '_', '-')]).strip()
        set_active_world(safe_name)
        flash(f"Switched to world: {safe_name}", "positive")
        
    return redirect("/")

@app.route("/rename_world", methods=["POST"])
def rename_world():
    global mc_process
    
    # Stop the server so things dont explode
    if mc_process is not None and mc_process.poll() is None:
        flash("Cannot rename a world while the server is running!", "negative")
        return redirect("/")
        
    new_name = request.form.get("new_world_name")
    if new_name:
        # Clean up the world name
        safe_name = "".join([c for c in new_name if c.isalpha() or c.isdigit() or c in (' ', '_', '-')]).strip()
        
        if not safe_name:
            flash("Invalid world name provided.", "negative")
            return redirect("/")
            
        paths = get_paths()
        old_folder = paths["cwd"]
        new_folder = os.path.join(INSTANCES_DIR, safe_name)
        
        # Check for world with same name
        if os.path.exists(new_folder):
            flash(f"A world named '{safe_name}' already exists!", "negative")
            return redirect("/")
            
        # Update the name and set the new active world
        os.rename(old_folder, new_folder)
        set_active_world(safe_name)
        
        flash(f"World successfully renamed to: {safe_name}", "positive")
        
    return redirect("/")

@app.route("/download", methods=["POST"])
def download():
    try:
        urllib.request.urlretrieve(get_latest_jar_url(), SERVER_JAR)
        flash("Latest Minecraft Server downloaded successfully!", "positive")
    except Exception as e:
        flash(f"Download failed: {e}", "negative")
    return redirect("/")

@app.route("/start", methods=["POST"])
def start():
    global mc_process
    paths = get_paths()
    
    if mc_process is None or mc_process.poll() is not None:
        if not os.path.exists(SERVER_JAR):
            flash("Server JAR not found. Download it first.", "negative")
            return redirect("/")
        
        # Auto accept the EULA in the specific instance folder before server first boot
        with open(paths["eula"], "w") as f:
            f.write("eula=true\n")
        
        log_handle = open(paths["log"], 'w')
        mc_process = subprocess.Popen(
            [get_java_path(), "-Xmx2G", "-Xms2G", "-jar", SERVER_JAR, "nogui"],
            cwd=paths["cwd"],
            stdin=subprocess.PIPE,
            stdout=log_handle,
            stderr=subprocess.STDOUT
        )
        flash(f"Starting world: {paths['name']}!", "positive")
    else:
        flash("Server is already running.", "information")
    return redirect("/")

@app.route("/log")
def get_log():
    paths = get_paths()
    if not os.path.exists(paths["log"]):
        return "Waiting for server to start..."
    
    with open(paths["log"], 'r') as f:
        lines = f.readlines()
        return "".join(lines[-100:])

@app.route("/stop", methods=["POST"])
def stop():
    global mc_process
    if mc_process and mc_process.poll() is None:
        mc_process.stdin.write(b"stop\n")
        mc_process.stdin.flush()
        mc_process.wait(timeout=30)
        flash("Server saved and shut down gracefully.", "positive")
    return redirect("/")

@app.route("/backup", methods=["POST"])
def backup():
    paths = get_paths()
    if os.path.exists(paths["world"]):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"world_backup_{timestamp}"
        backup_path = os.path.join(paths["backups"], backup_name)
        
        shutil.make_archive(backup_path, 'zip', paths["world"])
        flash(f"World backed up as {backup_name}.zip!", "positive")
    else:
        flash("No world found to backup yet. Start the server first!", "negative")
    return redirect("/")

@app.route("/delete_backup/<backup_filename>", methods=["POST"])
def delete_backup(backup_filename):
    paths = get_paths()
    backup_path = os.path.join(paths["backups"], backup_filename)
    if os.path.exists(backup_path):
        os.remove(backup_path)
        flash(f"Backup {backup_filename} successfully deleted.", "positive")
    else:
        flash("Backup file not found.", "negative")
    return redirect("/")

@app.route("/restore/<backup_filename>", methods=["POST"])
def restore(backup_filename):
    global mc_process
    paths = get_paths()
    backup_path = os.path.join(paths["backups"], backup_filename)
    
    if os.path.exists(backup_path):
        if mc_process and mc_process.poll() is None:
            mc_process.stdin.write(b"stop\n")
            mc_process.stdin.flush()
            mc_process.wait(timeout=30)
            
        if os.path.exists(paths["world"]):
            shutil.rmtree(paths["world"])
            
        shutil.unpack_archive(backup_path, paths["world"], 'zip')
        
        log_handle = open(paths["log"], 'w')
        mc_process = subprocess.Popen(
            [get_java_path(), "-Xmx2G", "-Xms2G", "-jar", SERVER_JAR, "nogui"],
            cwd=paths["cwd"],
            stdin=subprocess.PIPE,
            stdout=log_handle,
            stderr=subprocess.STDOUT
        )
        flash(f"World successfully restored! Server is spinning back up.", "positive")
    else:
        flash("Backup file not found.", "negative")
    return redirect("/")

@app.route("/command", methods=["POST"])
def command():
    global mc_process
    cmd = request.form.get("cmd")
    if mc_process and mc_process.poll() is None and cmd:
        try:
            mc_process.stdin.write(f"{cmd}\n".encode("utf-8"))
            mc_process.stdin.flush()
            flash(f"Command sent: /{cmd}", "positive")
        except Exception as e:
            flash(f"Failed to send command: {e}", "negative")
    else:
        flash("Cannot send command. Is the server running?", "negative")
    return redirect("/")

@app.route("/properties")
def properties():
    paths = get_paths()
    props = {}
    if os.path.exists(paths["properties"]):
        with open(paths["properties"], 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, val = line.split('=', 1)
                    props[key] = val
    else:
        flash("server.properties not found for this world yet. Start it once to generate.", "information")
        
    return render_template("properties.html", props=props, active_world=paths["name"])

@app.route("/save_properties", methods=["POST"])
def save_properties():
    paths = get_paths()
    props = {}
    
    # Read existing properties
    if os.path.exists(paths["properties"]):
        with open(paths["properties"], 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, val = line.split('=', 1)
                    props[key] = val
                    
    # Update with new stuff
    for key in request.form:
        props[key] = request.form[key]

    # Write back to the file
    with open(paths["properties"], 'w') as f:
        f.write(f"# Minecraft server properties for {paths['name']}\n")
        for key, val in props.items():
            f.write(f"{key}={val}\n")
            
    flash("Server properties updated! Restart the server to apply changes.", "positive")
    return redirect("/properties")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
