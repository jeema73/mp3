import os
import re
import sys
import time
import zipfile
import urllib.parse
from pathlib import Path
from typing import List, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import yt_dlp

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
STATIC_DIR = BASE_DIR / "static"
TEMPLATE_DIR = BASE_DIR / "templates"

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="기나미 MP3 추출기 - 4K High Quality Audio")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# 스레드 풀 (동시 변환 작업 최대 3개)
executor = ThreadPoolExecutor(max_workers=3)

# 메모리 내 작업 상태 저장소
# task_id: {"id": str, "title": str, "status": str, "percent": float, "speed": str, "eta": str, "filename": str, "filesize": str, "error": str}
tasks: Dict[str, Dict[str, Any]] = {}

def sanitize_filename(name: str) -> str:
    """윈도우 파일명 금지 문자 제거 및 안전한 파일명 생성"""
    name = re.sub(r'[\\/*?:"<>|]', '', name)
    name = name.strip()
    return name or "audio"

def format_duration(seconds: Optional[int]) -> str:
    """초 단위를 00:00 또는 00:00:00 문자열로 변환"""
    if not seconds:
        return "--:--"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def format_size(bytes_val: int) -> str:
    """바이트 단위를 MB 단위로 변환"""
    mb = bytes_val / (1024 * 1024)
    return f"{mb:.1f} MB"

class AnalyzeRequest(BaseModel):
    url: str

class ConvertTrackRequest(BaseModel):
    id: str
    url: str
    title: str
    bitrate: Optional[str] = "320"

class BatchConvertRequest(BaseModel):
    tracks: List[ConvertTrackRequest]
    bitrate: Optional[str] = "320"

class ZipRequest(BaseModel):
    title: str
    track_ids: List[str]


@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/status")
async def get_system_status():
    """시스템 상태 확인 (ffmpeg, yt-dlp, 저장소 용량)"""
    mp3_files = list(DOWNLOAD_DIR.glob("*.mp3"))
    zip_files = list(DOWNLOAD_DIR.glob("*.zip"))
    total_bytes = sum(f.stat().st_size for f in mp3_files + zip_files)
    
    return {
        "status": "online",
        "yt_dlp_version": yt_dlp.version.__version__,
        "cached_mp3_count": len(mp3_files),
        "cached_zip_count": len(zip_files),
        "total_storage_mb": round(total_bytes / (1024 * 1024), 2)
    }


@app.post("/api/analyze")
async def analyze_url(req: AnalyzeRequest):
    """유튜브 링크(단일 영상 또는 플레이리스트) 분석"""
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="유튜브 URL을 입력해주세요.")

    ydl_opts = {
        'extract_flat': 'in_playlist',
        'skip_download': True,
        'quiet': True,
        'no_warnings': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        error_msg = str(e)
        if "does not exist" in error_msg:
            detail = "존재하지 않거나 비공개된 유튜브 영상/재생목록입니다."
        elif "Private video" in error_msg:
            detail = "비공개 영상은 가져올 수 없습니다."
        else:
            detail = f"영상 정보를 불러오지 못했습니다: {error_msg}"
        raise HTTPException(status_code=400, detail=detail)

    if not info:
        raise HTTPException(status_code=404, detail="영상 정보를 찾을 수 없습니다.")

    # 플레이리스트 여부 판별
    is_playlist = info.get('_type') == 'playlist' or 'entries' in info
    playlist_title = info.get('title', 'YouTube Playlist') if is_playlist else None
    
    tracks = []
    
    if is_playlist:
        entries = info.get('entries') or []
        for entry in entries:
            if not entry:
                continue
            video_id = entry.get('id')
            title = entry.get('title') or "제목 없음"
            duration = entry.get('duration')
            uploader = entry.get('uploader') or entry.get('channel') or "YouTube"
            
            # 썸네일
            thumbnail = entry.get('thumbnail')
            if not thumbnail and video_id:
                thumbnail = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
            
            # 비디오 URL 복원
            video_url = entry.get('url')
            if not video_url or not video_url.startswith('http'):
                video_url = f"https://www.youtube.com/watch?v={video_id}"
                
            # 이미 다운로드되어 있는 파일이 있는지 확인
            safe_title = sanitize_filename(title)
            existing_file = None
            for mp3_path in DOWNLOAD_DIR.glob(f"*{video_id}*.mp3"):
                existing_file = mp3_path.name
                break
                
            tracks.append({
                "id": video_id,
                "title": title,
                "uploader": uploader,
                "duration": duration,
                "duration_str": format_duration(duration),
                "thumbnail": thumbnail,
                "url": video_url,
                "is_downloaded": existing_file is not None,
                "filename": existing_file
            })
    else:
        video_id = info.get('id')
        title = info.get('title') or "제목 없음"
        duration = info.get('duration')
        uploader = info.get('uploader') or info.get('channel') or "YouTube"
        thumbnail = info.get('thumbnail') or f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
        
        safe_title = sanitize_filename(title)
        existing_file = None
        for mp3_path in DOWNLOAD_DIR.glob(f"*{video_id}*.mp3"):
            existing_file = mp3_path.name
            break

        tracks.append({
            "id": video_id,
            "title": title,
            "uploader": uploader,
            "duration": duration,
            "duration_str": format_duration(duration),
            "thumbnail": thumbnail,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "is_downloaded": existing_file is not None,
            "filename": existing_file
        })

    return {
        "success": True,
        "is_playlist": is_playlist,
        "title": playlist_title or tracks[0]["title"],
        "uploader": info.get('uploader') or info.get('channel') or "",
        "total_count": len(tracks),
        "tracks": tracks
    }


def download_and_convert_task(track_id: str, url: str, title: str, bitrate: str):
    """백그라운드에서 실제 다운로드 및 고음질 MP3 변환 수행"""
    tasks[track_id] = {
        "id": track_id,
        "title": title,
        "status": "downloading",
        "percent": 0.0,
        "speed": "--",
        "eta": "--",
        "filename": None,
        "filesize": None,
        "error": None
    }

    safe_title = sanitize_filename(title)
    # 파일명 템플릿: [곡제목]_[영상ID].mp3
    out_tmpl = str(DOWNLOAD_DIR / f"{safe_title} [{track_id}].%(ext)s")

    def progress_hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded = d.get('downloaded_bytes') or 0
            if total > 0:
                percent = round((downloaded / total) * 100, 1)
            else:
                p_str = d.get('_percent_str', '0%').replace('%', '').strip()
                try:
                    percent = float(p_str)
                except:
                    percent = 0.0
            
            # 변환 단계가 남아있으므로 다운로드는 최대 92%까지만 표시
            display_percent = min(percent * 0.92, 92.0)
            
            tasks[track_id].update({
                "status": "downloading",
                "percent": round(display_percent, 1),
                "speed": d.get('_speed_str', '--'),
                "eta": d.get('_eta_str', '--')
            })
        elif d['status'] == 'finished':
            tasks[track_id].update({
                "status": "converting",
                "percent": 95.0,
                "speed": "FFmpeg 변환 중...",
                "eta": "마무리 단계"
            })

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': out_tmpl,
        'windowsfilenames': True,
        'quiet': True,
        'no_warnings': True,
        'postprocessors': [
            {
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': bitrate,
            },
            {
                'key': 'FFmpegMetadata',
                'add_metadata': True,
            },
            {
                'key': 'EmbedThumbnail',
                'already_have_thumbnail': False,
            }
        ],
        'writethumbnail': True,
        'progress_hooks': [progress_hook],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # 완료된 MP3 파일 검색
        target_mp3 = None
        for file in DOWNLOAD_DIR.glob(f"*{track_id}*.mp3"):
            target_mp3 = file
            break

        if target_mp3 and target_mp3.exists():
            filesize = format_size(target_mp3.stat().st_size)
            tasks[track_id].update({
                "status": "completed",
                "percent": 100.0,
                "speed": "완료",
                "eta": "00:00",
                "filename": target_mp3.name,
                "filesize": filesize
            })
        else:
            tasks[track_id].update({
                "status": "error",
                "error": "변환된 MP3 파일을 찾을 수 없습니다."
            })
    except Exception as e:
        tasks[track_id].update({
            "status": "error",
            "error": str(e)
        })


@app.post("/api/convert")
async def start_convert(req: ConvertTrackRequest):
    """단일 트랙 변환 요청"""
    bitrate = req.bitrate or "320"
    executor.submit(download_and_convert_task, req.id, req.url, req.title, bitrate)
    return {"success": True, "message": "변환 작업이 시작되었습니다.", "track_id": req.id}


@app.post("/api/convert-batch")
async def start_batch_convert(req: BatchConvertRequest):
    """여러 트랙 일괄 변환 요청"""
    bitrate = req.bitrate or "320"
    for track in req.tracks:
        executor.submit(download_and_convert_task, track.id, track.url, track.title, bitrate)
    return {"success": True, "message": f"{len(req.tracks)}곡 변환 작업이 큐에 추가되었습니다."}


@app.get("/api/progress/{track_id}")
async def get_progress(track_id: str):
    """특정 트랙의 실시간 변환 진행률 반환"""
    if track_id not in tasks:
        # 이미 이전에 다운로드된 파일이 있는지 확인
        for file in DOWNLOAD_DIR.glob(f"*{track_id}*.mp3"):
            filesize = format_size(file.stat().st_size)
            return {
                "id": track_id,
                "status": "completed",
                "percent": 100.0,
                "speed": "완료",
                "eta": "00:00",
                "filename": file.name,
                "filesize": filesize,
                "error": None
            }
        return {"id": track_id, "status": "pending", "percent": 0.0, "speed": "--", "eta": "--"}
    
    return tasks[track_id]


@app.get("/api/progress-all")
async def get_all_progress():
    """모든 활성 작업의 진행 상태 일괄 반환"""
    return tasks


@app.post("/api/zip")
async def create_zip_archive(req: ZipRequest):
    """완료된 트랙들을 1개의 ZIP 파일로 묶어서 다운로드 준비"""
    safe_title = sanitize_filename(req.title) or "playlist"
    zip_filename = f"{safe_title}_전곡_MP3.zip"
    zip_path = DOWNLOAD_DIR / zip_filename

    matched_files = []
    for track_id in req.track_ids:
        for mp3_file in DOWNLOAD_DIR.glob(f"*{track_id}*.mp3"):
            matched_files.append(mp3_file)
            break

    if not matched_files:
        raise HTTPException(status_code=400, detail="압축할 완료된 MP3 파일이 없습니다.")

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file in matched_files:
            # zip 파일 내에는 깔끔하게 원래 곡명만 표시
            clean_arcname = re.sub(r'\s*\[.*?\]\.mp3$', '.mp3', file.name)
            zipf.write(file, arcname=clean_arcname)

    filesize = format_size(zip_path.stat().st_size)
    
    return {
        "success": True,
        "zip_filename": zip_filename,
        "download_url": f"/api/download/{urllib.parse.quote(zip_filename)}",
        "file_count": len(matched_files),
        "filesize": filesize
    }


@app.get("/api/download/{filename:path}")
async def download_file(filename: str):
    """MP3 또는 ZIP 파일 다운로드 (한글 파일명 완벽 지원)"""
    decoded_name = urllib.parse.unquote(filename)
    file_path = DOWNLOAD_DIR / decoded_name

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="요청한 파일이 존재하지 않습니다.")

    # RFC 5987 규격으로 한글 파일명 인코딩 헤더 생성
    quoted_name = urllib.parse.quote(file_path.name)
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quoted_name}"
    }

    media_type = "application/zip" if file_path.suffix == ".zip" else "audio/mpeg"
    return FileResponse(path=str(file_path), media_type=media_type, headers=headers)


@app.get("/api/stream/{filename:path}")
async def stream_audio(filename: str):
    """웹 브라우저에서 MP3 바로 미리듣기 스트리밍"""
    decoded_name = urllib.parse.unquote(filename)
    file_path = DOWNLOAD_DIR / decoded_name

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="오디오 파일을 찾을 수 없습니다.")

    return FileResponse(path=str(file_path), media_type="audio/mpeg")


@app.delete("/api/clean-cache")
async def clean_cache():
    """임시 다운로드 파일 청소 (디스크 용량 확보)"""
    deleted_count = 0
    for f in DOWNLOAD_DIR.glob("*.*"):
        try:
            f.unlink()
            deleted_count += 1
        except Exception:
            pass
    tasks.clear()
    return {"success": True, "deleted_count": deleted_count}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8088, reload=True)
