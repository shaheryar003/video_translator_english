from fastapi import FastAPI, File, UploadFile, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import shutil
import os
import uuid
import translate_video  # Check if this import works, assuming it's in the same dir
import asyncio

app = FastAPI()

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Store task status in memory (for simplicity)
# In production, use Redis/Database
tasks = {}

OUTPUT_DIR = "Output"
UPLOAD_DIR = "uploads"

if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

def process_video_task(task_id: str, file_path: str):
    tasks[task_id] = {"status": "processing", "message": "Starting translation process..."}
    try:
        # Call the actual processing function
        tasks[task_id]["message"] = "Processing video segments... This may take several minutes."
        
        output_file = translate_video.process_single_video(file_path, OUTPUT_DIR, file_id=task_id)
        
        if output_file and os.path.exists(output_file):
            tasks[task_id] = {
                "status": "completed", 
                "message": "Translation complete!", 
                "file_url": f"/download/{os.path.basename(output_file)}"
            }
        else:
            tasks[task_id] = {"status": "failed", "message": "Processing failed internally. Please check logs."}
            
    except Exception as e:
        tasks[task_id] = {"status": "failed", "message": f"Error: {str(e)}"}
    finally:
        # Cleanup upload
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except:
                pass

@app.get("/")
async def read_index():
    return FileResponse("static/index.html")

@app.post("/upload")
async def upload_video(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    # Validate file type
    if not file.filename.endswith(".mp4"):
        return {"error": "Only MP4 files are allowed currently."}

    task_id = str(uuid.uuid4())
    safe_filename = "".join(c for c in file.filename if c.isalnum() or c in "._-")
    file_path = os.path.join(UPLOAD_DIR, f"{task_id}_{safe_filename}")
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    tasks[task_id] = {"status": "queued", "message": "File uploaded successfully. queued for processing."}
    
    background_tasks.add_task(process_video_task, task_id, file_path)
    
    return {"task_id": task_id}

@app.get("/status/{task_id}")
async def get_status(task_id: str):
    return tasks.get(task_id, {"status": "not_found"})

@app.get("/download/{filename}")
async def download_file(filename: str):
    file_path = os.path.join(OUTPUT_DIR, filename)
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="video/mp4", filename=filename)
    return {"error": "File not found"}
