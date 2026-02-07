import os
import time
import sys
from openai import OpenAI
from moviepy.editor import VideoFileClip, AudioFileClip, CompositeAudioClip, concatenate_videoclips
from moviepy.video.io.ffmpeg_tools import ffmpeg_extract_subclip

import math
import shutil

# --- Configuration ---
API_KEY = os.environ.get("OPENAI_API_KEY")
if not API_KEY:
    # Fallback to the hardcoded one if not in env, though it's recommended to use env vars
    API_KEY = "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"

# Voice for TTS (alloy, ash, coral, echo, fable, onyx, nova, shimmer)
TTS_VOICE = "onyx" 
TTS_MODEL = "tts-1" 

# Initialize OpenAI Client
client = OpenAI(api_key=API_KEY)

def safe_print(text):
    """Safely prints text avoiding Unicode errors in Windows console."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode('utf-8', 'replace').decode('utf-8'))

def get_video_files():
    """Finds all mp4 files in the Videos directory."""
    video_dir = "Videos"
    if not os.path.exists(video_dir):
        os.makedirs(video_dir)
        safe_print(f"Created '{video_dir}' directory. Please put your video files there.")
        return []
        
    files = [os.path.join(video_dir, f) for f in os.listdir(video_dir) if f.endswith('.mp4') and not f.startswith("temp_split_")]
    return files

def extract_audio(video_path, audio_path):
    """Extracts audio from video using moviepy."""
    safe_print(f"Extracting audio from {video_path}...")
    try:
        video = VideoFileClip(video_path)
        # extracting at 32k bitrate, mono channel to reduce size
        video.audio.write_audiofile(audio_path, bitrate="32k", ffmpeg_params=["-ac", "1"], logger=None)
        video.close()
        return True
    except Exception as e:
        safe_print(f"Error extracting audio: {e}")
        return False

def format_timestamp(seconds):
    """Formats seconds into SRT timestamp format (HH:MM:SS,mmm)."""
    millis = int((seconds - int(seconds)) * 1000)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = int(seconds % 60)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"

def generate_srt(segments, output_path):
    """Generates an SRT file from Whisper segments."""
    safe_print(f"Generating subtitles to {output_path}...")
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            for i, segment in enumerate(segments):
                start = format_timestamp(segment['start'])
                end = format_timestamp(segment['end'])
                text = segment['text'].strip()
                
                f.write(f"{i+1}\n")
                f.write(f"{start} --> {end}\n")
                f.write(f"{text}\n\n")
        return True
    except Exception as e:
        safe_print(f"Error creating SRT: {e}")
        return False

def transcribe_audio_chunk(audio_path, offset_seconds=0):
    """Transcribes a single audio file and applies time offset to segments."""
    for attempt in range(3):
        try:
            with open(audio_path, "rb") as audio_file:
                response = client.audio.translations.create(
                    model="whisper-1", 
                    file=audio_file,
                    response_format="verbose_json"
                )
            
            text = response.text
            segments = response.segments if hasattr(response, 'segments') else []
            
            adjusted_segments = []
            for seg in segments:
                # Handle object access (OpenAI v1+)
                if hasattr(seg, 'start'):
                    s_start = seg.start
                    s_end = seg.end
                    s_text = seg.text
                # Handle dictionary access (Legacy or raw dicts)
                elif isinstance(seg, dict):
                    s_start = seg.get('start', 0)
                    s_end = seg.get('end', 0)
                    s_text = seg.get('text', '')
                else:
                    continue

                adjusted_segments.append({
                    "start": s_start + offset_seconds,
                    "end": s_end + offset_seconds,
                    "text": s_text
                })
                
            return text, adjusted_segments
        except Exception as e:
            safe_print(f"Error transcribing chunk {audio_path} (Attempt {attempt+1}): {e}")
            time.sleep(2)
            
    return None, []

def process_audio_translation(audio_path):
    """Handles transcription, supporting large files by chunking."""
    safe_print("Starting transcription/translation...")
    
    file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    
    # OpenAI limit is 25MB. We use 24MB to be safe.
    if file_size_mb < 24:
        return transcribe_audio_chunk(audio_path)
    
    safe_print(f"File is large ({file_size_mb:.2f} MB). Splitting into 10-minute chunks...")
    
    full_text = ""
    all_segments = []
    
    try:
        # Use AudioFileClip for precise cutting (ffmpeg tools can drift)
        clip = AudioFileClip(audio_path)
        duration = clip.duration
        
        chunk_length = 600 # 10 minutes
        total_chunks = math.ceil(duration / chunk_length)
        
        temp_chunk_files = []
        
        for i in range(total_chunks):
            start = i * chunk_length
            end = min((i + 1) * chunk_length, duration)
            
            chunk_filename = f"temp_chunk_{i}.mp3"
            temp_chunk_files.append(chunk_filename)
            
            safe_print(f"Extracting chunk {i+1}/{total_chunks} ({start}-{end}s)...")
            
            # Precise cut
            sub = clip.subclip(start, end)
            sub.write_audiofile(chunk_filename, bitrate="64k", logger=None)
            # Close subclip to free memory
            sub.close() 
            
            safe_print(f"Transcribing chunk {i+1}...")
            text, segments = transcribe_audio_chunk(chunk_filename, offset_seconds=start)
            
            if text:
                full_text += text + " "
                all_segments.extend(segments)
        
        clip.close()
            
        # Cleanup chunks
        for f in temp_chunk_files:
            if os.path.exists(f): os.remove(f)
            
        return full_text.strip(), all_segments

    except Exception as e:
        safe_print(f"Error processing large audio: {e}")
        return None, None

def generate_synced_voice_over(segments, output_audio_path, total_duration=None, unique_id=""):
    """Generates synced audio. Handles file handle limits by batching."""
    safe_print("Generating synced English voice-over...")
    
    temp_dir = f"temp_segments_{unique_id}" if unique_id else "temp_segments"
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)
        
    temp_files = [] 
    
    BATCH_SIZE = 50
    final_clips_batches = []
    
    try:
        current_batch_clips = []
        
        for i, segment in enumerate(segments):
            text = segment['text'].strip()
            if not text: continue
            
            start_time = segment['start']
            end_time = segment['end']
            
            clean_text = "".join(c for c in text[:10] if c.isalnum()) or "audio"
            segment_file = os.path.join(temp_dir, f"seg_{i}_{clean_text}.mp3")
            temp_files.append(segment_file)
            
            safe_print(f"  - Generating segment {i+1}/{len(segments)} ({start_time:.1f}s)")
            
            # Retry logic for TTS
            success = False
            for attempt in range(3):
                try:
                    response = client.audio.speech.create(
                        model=TTS_MODEL,
                        voice=TTS_VOICE,
                        input=text
                    )
                    response.stream_to_file(segment_file)
                    success = True
                    break
                except Exception as e:
                    time.sleep(1)
            
            if not success:
                safe_print(f"    Failed to generate segment {i}. Skipping.")
                continue

            # Process Clip
            try:
                clip = AudioFileClip(segment_file)
                clip_duration = clip.duration
                target_duration = end_time - start_time
                
                speed_factor = 1.0
                if clip_duration > target_duration:
                    ratio = clip_duration / target_duration
                    # Speed up max 1.35x
                    speed_factor = min(ratio, 1.35)
                    new_dur = clip_duration / speed_factor
                    clip = clip.fl_time(lambda t: speed_factor * t, apply_to=['mask', 'audio'])
                    clip = clip.set_duration(new_dur)
                
                clip = clip.set_start(start_time)
                current_batch_clips.append(clip)
                
            except Exception as e:
                safe_print(f"    Error processing clip {i}: {e}")
        
        if not current_batch_clips:
            return False

        # Composite all together
        safe_print("Merging audio segments...")
        final_audio = CompositeAudioClip(current_batch_clips)
        
        if total_duration:
            final_audio = final_audio.set_duration(total_duration)
            
        final_audio.write_audiofile(output_audio_path, fps=44100, logger="bar")
        
        # Close all clips
        for clip in current_batch_clips:
            clip.close()
        final_audio.close()
        
        # Cleanup
        shutil.rmtree(temp_dir, ignore_errors=True)
        return True
        
    except Exception as e:
        safe_print(f"Error generating synced TTS: {e}")
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except: pass
        return False

def merge_audio_video(video_path, audio_path, output_path):
    """Merges the new audio with the original video."""
    safe_print("Merging new audio with video...")
    try:
        video = VideoFileClip(video_path)
        new_audio = AudioFileClip(audio_path)
        
        final_audio = new_audio
        if new_audio.duration > video.duration:
             final_audio = new_audio.subclip(0, video.duration)
        
        # Use set_audio (older API compatible) instead of with_audio
        final_video = video.set_audio(final_audio)
        
        final_video.write_videofile(
            output_path, 
            codec="libx264", 
            audio_codec="aac",
            logger="bar"
        )
        
        video.close()
        new_audio.close()
        final_video.close()
        return True
    except Exception as e:
        safe_print(f"Error merging video: {e}")
        return False

def split_video(video_path, num_parts=4):
    """Splits a video into equal parts using ffmpeg_extract_subclip (faster, no re-encode)."""
    safe_print(f"Splitting video into {num_parts} parts...")
    try:
        # We need duration. VideoFileClip is fast at reading metadata.
        clip = VideoFileClip(video_path)
        duration = clip.duration
        clip.close()
        
        part_duration = duration / num_parts
        
        parts = []
        video_dir = os.path.dirname(video_path)
        base_name = os.path.splitext(os.path.basename(video_path))[0]
        
        for i in range(num_parts):
            start = i * part_duration
            end = (i + 1) * part_duration if i < num_parts - 1 else duration
            
            output_name = os.path.join(video_dir, f"temp_split_{base_name}_{i}.mp4")
            
            # Use ffmpeg_tools to cut.
            try:
                ffmpeg_extract_subclip(video_path, start, end, targetname=output_name)
                parts.append(output_name)
            except Exception as e:
                safe_print(f"Error cutting part {i}: {e}")
                return []
            
        return parts
    except Exception as e:
        safe_print(f"Error splitting video: {e}")
        return []

def combine_videos(video_paths, output_path):
    """Combines multiple video files into one."""
    safe_print("Combining video parts...")
    clips = []
    try:
        for path in video_paths:
            clips.append(VideoFileClip(path))
            
        final_clip = concatenate_videoclips(clips)
        final_clip.write_videofile(
            output_path,
            codec="libx264",
            audio_codec="aac",
            logger="bar"
        )
        
        # Close clips
        for clip in clips:
            clip.close()
        final_clip.close()
        return True
    except Exception as e:
        safe_print(f"Error combining videos: {e}")
        return False

def process_video_part(video_file, part_index, base_temp_name):
    """
    Process a single video part: Extract -> Transcribe -> TTS -> Merge.
    Returns: (success, output_video_path, segments, duration)
    """
    safe_print(f"--- Processing Part {part_index+1} ---")
    
    temp_audio = f"{base_temp_name}_audio_{part_index}.mp3"
    temp_tts = f"{base_temp_name}_tts_{part_index}.mp3"
    output_video = f"{base_temp_name}_processed_{part_index}.mp4"
    unique_id = os.path.basename(base_temp_name)
    
    # 1. Extract Audio
    if not extract_audio(video_file, temp_audio):
        return False, None, [], 0
        
    # 2. Transcribe
    translated_text, segments = process_audio_translation(temp_audio)
    if not translated_text:
        safe_print(f"Part {part_index+1}: Translation failed.")
        if os.path.exists(temp_audio): os.remove(temp_audio)
        return False, None, [], 0
        
    # 3. Generate Voice over
    try:
        temp_clip = VideoFileClip(video_file)
        vid_duration = temp_clip.duration
        temp_clip.close()
    except:
        vid_duration = None

    if not generate_synced_voice_over(segments, temp_tts, total_duration=vid_duration, unique_id=unique_id):
        if os.path.exists(temp_audio): os.remove(temp_audio)
        return False, None, [], 0
        
    # 4. Merge
    success = merge_audio_video(video_file, temp_tts, output_video)
    
    # Cleanup temps
    if os.path.exists(temp_audio): os.remove(temp_audio)
    if os.path.exists(temp_tts): os.remove(temp_tts)
    
    if success:
        return True, output_video, segments, vid_duration
    else:
        return False, None, [], 0

def process_single_video(video_file, output_dir, file_id="file"):
    """
    Processes a single video file fully.
    """
    start_time = time.time()
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # 1. Split Video
    parts = split_video(video_file, num_parts=4)
    if not parts:
        safe_print("Failed to split video. Skipping.")
        return None
        
    processed_parts = []
    all_segments = []
    accumulated_time = 0.0
    
    # 2. Process Each Part
    task_failed = False
    filename = os.path.basename(video_file)
    name_without_ext = os.path.splitext(filename)[0]
    base_temp_name = f"temp_{name_without_ext}_{file_id}"
    
    for i, part_file in enumerate(parts):
        success, out_vid, segments, duration = process_video_part(part_file, i, base_temp_name)
        if not success:
            task_failed = True
            break
        
        processed_parts.append(out_vid)
        
        # Adjust segment timestamps
        for seg in segments:
            seg['start'] += accumulated_time
            seg['end'] += accumulated_time
            all_segments.append(seg)
            
        accumulated_time += duration
        
    if task_failed:
        safe_print("Error occurred in one of the parts. Aborting this video.")
        # Cleanup
        for p in parts + processed_parts:
            if os.path.exists(p): os.remove(p)
        return None
        
    # 3. Combine Processed Parts
    final_output_video = os.path.join(output_dir, f"{name_without_ext}_translated.mp4")
    success_combine = combine_videos(processed_parts, final_output_video)
    
    # Cleanup
    for p in parts + processed_parts:
        if os.path.exists(p): os.remove(p)

    if success_combine:
        safe_print(f"SUCCESS! Video saved to: {final_output_video}")
        return final_output_video
    
    return None

def main():
    if not API_KEY or "YOUR_API_KEY" in API_KEY:
        safe_print("ERROR: API_KEY is not set. Please set the API_KEY variable in the script or environment.")
        return

    video_files = get_video_files()
    if not video_files:
        safe_print("No MP4 files found in 'Videos' folder.")
        return

    output_dir = "Output"
    safe_print(f"Found {len(video_files)} video(s) to process.")

    for index, video_file in enumerate(video_files):
        safe_print("\n" + "="*60)
        safe_print(f"PROCESSING VIDEO {index+1}/{len(video_files)}: {video_file}")
        safe_print("="*60)
        
        process_single_video(video_file, output_dir, file_id=str(index))

    safe_print("\nAll tasks completed.")

if __name__ == "__main__":
    main()
