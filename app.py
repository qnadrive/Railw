from flask import Flask, request, jsonify
import requests
import re
import os
import threading
import uuid
import time
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

PIXELDRAIN_API_KEY = os.getenv("PIXELDRAIN_API_KEY")
WORDPRESS_UPDATE_URL = os.getenv("WORDPRESS_UPDATE_URL", "https://mehedilogy.com/wp-json/pd/v1/update")

jobs = {}

def get_file_id(url):
    if "drive.google.com/file/d/" in url:
        return re.search(r"/file/d/([a-zA-Z0-9_-]+)", url).group(1)
    elif "id=" in url:
        return re.search(r"id=([a-zA-Z0-9_-]+)", url).group(1)
    return None

def get_gdrive_stream(file_id):
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0'})
    url = f"https://docs.google.com/uc?export=download&id={file_id}"
    response = session.get(url, allow_redirects=True, timeout=60)
    
    if "virus scan warning" in response.text.lower() or "download anyway" in response.text.lower():
        confirm_match = re.search(r'name="confirm"\s+value="([a-zA-Z0-9_-]+)"', response.text)
        confirm = confirm_match.group(1) if confirm_match else "t"
        
        download_url = "https://drive.usercontent.google.com/download"
        params = {"id": file_id, "export": "download", "confirm": confirm}
        
        return session.get(download_url, params=params, stream=True, timeout=1800)
    
    return response

def background_upload(job_id, file_id, custom_name=None):
    jobs[job_id]['status'] = 'running'
    try:
        print(f"[{job_id}] Background upload started")
        gdrive_response = get_gdrive_stream(file_id)
        
        if not custom_name:
            custom_name = f"file_{file_id[:12]}.mkv"
        
        # Pixeldrain upload with streaming (chunked)
        auth = requests.auth.HTTPBasicAuth('', PIXELDRAIN_API_KEY)
        upload_url = "https://pixeldrain.com/api/file"
        
        def generate_chunks():
            for chunk in gdrive_response.iter_content(chunk_size=8*1024*1024):  # 8MB chunks
                if chunk:
                    yield chunk
        
        r = requests.post(upload_url, data=generate_chunks(), auth=auth, timeout=3600)
        
        if r.status_code == 201:
            result = r.json()
            pd_id = result.get('id')
            pd_link = f"https://pixeldrain.com/api/file/{pd_id}?download"
            
            jobs[job_id]['status'] = 'done'
            jobs[job_id]['pd_id'] = pd_id
            jobs[job_id]['pd_link'] = pd_link
            
            # WordPress update
            if WORDPRESS_UPDATE_URL:
                try:
                    wp_payload = {
                        "unique_id": job_id,
                        "pd_id": pd_id,
                        "pd_direct_link": pd_link,
                        "original_name": custom_name
                    }
                    requests.post(WORDPRESS_UPDATE_URL, json=wp_payload, timeout=20)
                    print(f"[{job_id}] WordPress updated successfully")
                except:
                    print(f"[{job_id}] WordPress update failed")
        else:
            raise Exception(f"Pixeldrain error: {r.status_code}")
            
    except Exception as e:
        jobs[job_id]['status'] = 'failed'
        jobs[job_id]['error'] = str(e)
        print(f"[{job_id}] Error: {e}")

@app.route("/api/submit", methods=["POST"])
def api_submit():
    data = request.get_json() or {}
    gd_link = data.get("link")
    custom_name = data.get("name", "").strip()
    
    if not gd_link:
        return jsonify({"error": "Google Drive link required"}), 400
    
    file_id = get_file_id(gd_link)
    if not file_id:
        return jsonify({"error": "Invalid Google Drive link"}), 400
    
    job_id = str(uuid.uuid4())
    jobs[job_id] = {'status': 'queued', 'pd_link': None, 'error': None}
    
    thread = threading.Thread(target=background_upload, args=(job_id, file_id, custom_name))
    thread.daemon = True
    thread.start()
    
    return jsonify({
        "success": True,
        "job_id": job_id,
        "message": "Upload queued. Check /api/status/" + job_id
    })

@app.route("/api/status/<job_id>")
def api_status(job_id):
    if job_id not in jobs:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(jobs[job_id])

@app.route("/")
def home():
    return "GD to Pixeldrain Remote Uploader is running on Railway!<br>Use /api/submit"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
