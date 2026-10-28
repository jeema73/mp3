import os
import re
import sys
import time
import zipfile
import urllib.parse
import subprocess
from pathlib import Path
from typing import List, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import yt_dlp
from mutagen.id3 import ID3, TIT2, TPE1, TRCK, APIC
from mutagen.mp3 import MP3

from tunnel_manager import start_tunnel_background, get_tunnel_info

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

# 비동기 변환 스레드 풀
executor = ThreadPoolExecutor(max_workers=3)

# 메모리 내 작업 상태 저장소
tasks: Dict[str, Dict[str, Any]] = {}

def sanitize_filename(name: str) -> str:
    """윈도우 파일명 금지 문자 제거 및 안전한 파일명 생성"""
    name = re.sub(r'[\\/*?:"<>|]', '', name)
    name = re.sub(r'\s+', ' ', name)
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

def parse_description_timestamps(desc: str, total_duration: Optional[int] = None) -> List[Dict[str, Any]]:
    """유튜브 설명란에서 00:00 형태의 곡별 타임스탬프 자동 파싱"""
    if not desc:
        return []
    
    lines = desc.split('\n')
    raw_tracks = []
    
    # 01:23 곡명 또는 [01:23] 곡명 또는 곡명 01:23 매칭
    pattern_start = re.compile(r'^\s*(?:\[|\()?(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:\]|\))?\s*[-–—~|.]?\s*(.+)$')
    pattern_end = re.compile(r'^\s*(.+?)\s*[-–—~|.]?\s*(?:\[|\()?(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:\]|\))?\s*$')

    for line in lines:
        line = line.strip()
        if not line:
            continue
        m1 = pattern_start.match(line)
        if m1:
            h, m, s, title = m1.groups()
            sec = (int(h) * 3600 if h else 0) + int(m) * 60 + int(s)
            raw_tracks.append({"start_time": sec, "title": title.strip()})
            continue
        m2 = pattern_end.match(line)
        if m2:
            title, h, m, s = m2.groups()
            sec = (int(h) * 3600 if h else 0) + int(m) * 60 + int(s)
            raw_tracks.append({"start_time": sec, "title": title.strip()})
    
    if len(raw_tracks) < 2:
        return []

    # 시간순 정렬
    raw_tracks.sort(key=lambda x: x["start_time"])
    
    # 각 곡의 종료 시간(end_time) 계산
    chapters = []
    for i in range(len(raw_tracks)):
        st = raw_tracks[i]["start_time"]
        if i + 1 < len(raw_tracks):
            et = raw_tracks[i + 1]["start_time"]
        else:
            et = total_duration or (st + 240)
        
        # 유효한 길이인지 검증
        if et > st:
            chapters.append({
                "title": raw_tracks[i]["title"],
                "start_time": st,
                "end_time": et
            })
            
    return chapters if len(chapters) >= 2 else []


class AnalyzeRequest(BaseModel):
    url: str

class ConvertTrackRequest(BaseModel):
    id: str
    url: str
    title: str
    bitrate: Optional[str] = "320"
    parent_id: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    chapter_index: Optional[int] = None
    uploader: Optional[str] = None

class BatchConvertRequest(BaseModel):
    tracks: List[ConvertTrackRequest]
    bitrate: Optional[str] = "320"

class ZipRequest(BaseModel):
    title: str
    track_ids: List[str]

class SilenceSplitRequest(BaseModel):
    video_id: str
    url: str
    title: str
    duration: int
    uploader: Optional[str] = "YouTube"
    thumbnail: Optional[str] = ""
    noise_db: Optional[int] = -30
    min_silence_sec: Optional[float] = 1.2

class EqualSplitRequest(BaseModel):
    video_id: str
    url: str
    title: str
    duration: int
    uploader: Optional[str] = "YouTube"
    thumbnail: Optional[str] = ""
    count: int = 30

class TextTimestampSplitRequest(BaseModel):
    video_id: str
    url: str
    title: str
    duration: int
    uploader: Optional[str] = "YouTube"
    thumbnail: Optional[str] = ""
    text: str


@app.on_event("startup")
async def startup_event():
    # 백그라운드에서 Cloudflare 터널 실행
    start_tunnel_background(port=8088)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": f"서버 내부 오류: {str(exc)}"})

@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/api/tunnel")
async def get_tunnel_status():
    """스마트폰 접속용 Cloudflare 터널 주소 및 QR 정보 반환"""
    return get_tunnel_info()


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
    """유튜브 링크(단일 영상, 플레이리스트, 또는 다중 곡 믹스) 분석"""
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
    has_chapters = False
    chapters_list = []
    
    if is_playlist:
        entries = info.get('entries') or []
        for entry in entries:
            if not entry:
                continue
            video_id = entry.get('id')
            title = entry.get('title') or "제목 없음"
            duration = entry.get('duration')
            uploader = entry.get('uploader') or entry.get('channel') or "YouTube"
            
            thumbnail = entry.get('thumbnail')
            if not thumbnail and video_id:
                thumbnail = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
            
            video_url = entry.get('url')
            if not video_url or not video_url.startswith('http'):
                video_url = f"https://www.youtube.com/watch?v={video_id}"
                
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
        
        # 챕터 감지 (유튜브 자체 챕터 또는 설명란 타임스탬프)
        raw_chapters = info.get('chapters') or []
        if not raw_chapters and info.get('description'):
            raw_chapters = parse_description_timestamps(info.get('description'), duration)
            
        if raw_chapters and len(raw_chapters) >= 2:
            has_chapters = True
            for idx, ch in enumerate(raw_chapters):
                st = ch.get('start_time', 0)
                et = ch.get('end_time', duration or (st + 240))
                ch_title = ch.get('title') or f"Track {idx+1}"
                ch_duration = int(et - st) if et else None
                ch_id = f"{video_id}_ch{idx+1}"
                
                # 이미 분리된 파일이 있는지 확인
                existing_ch_file = None
                for mp3_path in DOWNLOAD_DIR.glob(f"*{ch_id}*.mp3"):
                    existing_ch_file = mp3_path.name
                    break

                chapters_list.append({
                    "id": ch_id,
                    "parent_id": video_id,
                    "chapter_index": idx + 1,
                    "title": ch_title,
                    "uploader": uploader,
                    "start_time": st,
                    "end_time": et,
                    "duration": ch_duration,
                    "duration_str": format_duration(ch_duration),
                    "thumbnail": thumbnail,
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "is_downloaded": existing_ch_file is not None,
                    "filename": existing_ch_file
                })

        existing_file = None
        for mp3_path in DOWNLOAD_DIR.glob(f"*{video_id}*.mp3"):
            # 챕터 분리 파일이 아닌 원본 통 파일인지 확인
            if "_ch" not in mp3_path.name:
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
        "has_chapters": has_chapters,
        "chapters_count": len(chapters_list),
        "chapters": chapters_list,
        "title": playlist_title or tracks[0]["title"],
        "uploader": info.get('uploader') or info.get('channel') or "",
        "total_count": len(tracks),
        "tracks": tracks
    }


def download_parent_audio(video_id: str, url: str, bitrate: str) -> Optional[Path]:
    """챕터 분리를 위해 부모 오디오를 전체 다운로드"""
    parent_file = None
    for f in DOWNLOAD_DIR.glob(f"*{video_id}*.mp3"):
        if "_ch" not in f.name:
            return f

    out_tmpl = str(DOWNLOAD_DIR / f"full_temp [{video_id}].%(ext)s")
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
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    for f in DOWNLOAD_DIR.glob(f"*{video_id}*.mp3"):
        if "_ch" not in f.name and "_s" not in f.name and "_eq" not in f.name and "_t" not in f.name:
            return f
    return None


def detect_silence_splits(audio_path: Path, min_silence_sec: float = 1.2, noise_db: int = -30) -> List[float]:
    """FFmpeg silencedetect 필터를 사용하여 음악 간 무음 종료 지점(곡 전환 시점) 감지"""
    cmd = [
        "ffmpeg", "-i", str(audio_path),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_sec}",
        "-f", "null", "-"
    ]
    res = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, errors="ignore")
    points = []
    for line in res.stderr.splitlines():
        if "silence_end:" in line:
            m = re.search(r'silence_end:\s*([0-9.]+)', line)
            if m:
                p = float(m.group(1))
                if not points or (p - points[-1]) >= 45.0:
                    points.append(p)
    return points


def download_and_convert_task(
    track_id: str, 
    url: str, 
    title: str, 
    bitrate: str,
    parent_id: Optional[str] = None,
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
    chapter_index: Optional[int] = None,
    uploader: Optional[str] = None
):
    """백그라운드에서 실제 다운로드 및 고음질 MP3 변환 또는 챕터 분리 수행"""
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

    # 1. 특정 챕터/구간 분리 요청인 경우
    if parent_id and start_time is not None and end_time is not None:
        try:
            tasks[track_id].update({
                "status": "downloading",
                "percent": 30.0,
                "speed": "원본 음원 준비 중...",
                "eta": "잠시만 기다려주세요"
            })
            
            # 부모 전체 오디오 확보
            parent_mp3 = download_parent_audio(parent_id, url, bitrate)
            if not parent_mp3 or not parent_mp3.exists():
                raise Exception("원본 음원을 다운로드하지 못했습니다.")

            tasks[track_id].update({
                "status": "converting",
                "percent": 80.0,
                "speed": f"곡 분리 중 ({format_duration(int(start_time))} ~ {format_duration(int(end_time))})",
                "eta": "FFmpeg 분할"
            })

            # 분리될 챕터 파일명
            chapter_filename = f"{safe_title} [{track_id}].mp3"
            chapter_path = DOWNLOAD_DIR / chapter_filename

            # FFmpeg 정확한 구간 슬라이싱 (-ss, -to)
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(start_time),
                "-to", str(end_time),
                "-i", str(parent_mp3),
                "-c:a", "libmp3lame",
                "-b:a", f"{bitrate}k",
                "-map_metadata", "0",
                str(chapter_path)
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode != 0:
                raise Exception(f"FFmpeg 분할 실패: {res.stderr.decode('utf-8', errors='ignore')[:200]}")

            # Mutagen으로 ID3 태그 주입
            try:
                audio = MP3(str(chapter_path), ID3=ID3)
                if audio.tags is None:
                    audio.add_tags()
                audio.tags.add(TIT2(encoding=3, text=title))
                if uploader:
                    audio.tags.add(TPE1(encoding=3, text=uploader))
                if chapter_index:
                    audio.tags.add(TRCK(encoding=3, text=str(chapter_index)))
                audio.save()
            except Exception as tag_err:
                print(f"태그 저장 경고: {tag_err}")

            filesize = format_size(chapter_path.stat().st_size)
            tasks[track_id].update({
                "status": "completed",
                "percent": 100.0,
                "speed": "완료",
                "eta": "00:00",
                "filename": chapter_filename,
                "filesize": filesize
            })
            return
        except Exception as e:
            tasks[track_id].update({
                "status": "error",
                "error": str(e)
            })
            return

    # 2. 일반 단일 영상/트랙 변환
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

        target_mp3 = None
        for file in DOWNLOAD_DIR.glob(f"*{track_id}*.mp3"):
            if "_ch" not in file.name:
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
    """단일 트랙 또는 챕터 분리 변환 요청"""
    bitrate = req.bitrate or "320"
    executor.submit(
        download_and_convert_task, 
        req.id, 
        req.url, 
        req.title, 
        bitrate,
        req.parent_id,
        req.start_time,
        req.end_time,
        req.chapter_index,
        req.uploader
    )
    return {"success": True, "message": "변환 작업이 시작되었습니다.", "track_id": req.id}


@app.post("/api/convert-batch")
async def start_batch_convert(req: BatchConvertRequest):
    """여러 트랙(또는 전 챕터) 일괄 변환 요청"""
    bitrate = req.bitrate or "320"
    for track in req.tracks:
        executor.submit(
            download_and_convert_task, 
            track.id, 
            track.url, 
            track.title, 
            bitrate,
            track.parent_id,
            track.start_time,
            track.end_time,
            track.chapter_index,
            track.uploader
        )
    return {"success": True, "message": f"{len(req.tracks)}곡 변환 작업이 큐에 추가되었습니다."}


@app.get("/api/progress/{track_id}")
async def get_progress(track_id: str):
    """특정 트랙/챕터의 실시간 변환 진행률 반환"""
    if track_id not in tasks:
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
    return tasks


@app.post("/api/split-silence")
async def split_by_silence(req: SilenceSplitRequest):
    """무음 구간 자동 감지를 통한 트랙 분리"""
    parent_mp3 = download_parent_audio(req.video_id, req.url, "320")
    if not parent_mp3 or not parent_mp3.exists():
        raise HTTPException(status_code=400, detail="음원을 다운로드하지 못했습니다.")
    
    noise_db = req.noise_db or -30
    min_silence = req.min_silence_sec or 1.2
    split_points = detect_silence_splits(parent_mp3, min_silence, noise_db)
    
    if len(split_points) < 3:
        split_points = detect_silence_splits(parent_mp3, 1.0, -26)
        
    duration = req.duration or 3600
    
    tracks = []
    all_points = [0.0] + split_points + [float(duration)]
    all_points = sorted(list(set(all_points)))
    
    for i in range(len(all_points) - 1):
        st = all_points[i]
        et = all_points[i+1]
        if (et - st) < 20:
            continue
        idx = len(tracks) + 1
        ch_id = f"{req.video_id}_s{idx}"
        ch_title = f"{req.title} - Track {idx:02d}"
        ch_dur = int(et - st)
        
        existing_ch_file = None
        for mp3_path in DOWNLOAD_DIR.glob(f"*{ch_id}*.mp3"):
            existing_ch_file = mp3_path.name
            break
            
        tracks.append({
            "id": ch_id,
            "parent_id": req.video_id,
            "chapter_index": idx,
            "title": ch_title,
            "uploader": req.uploader,
            "start_time": st,
            "end_time": et,
            "duration": ch_dur,
            "duration_str": format_duration(ch_dur),
            "thumbnail": req.thumbnail,
            "url": req.url,
            "is_downloaded": existing_ch_file is not None,
            "filename": existing_ch_file
        })
        
    return {
        "success": True,
        "count": len(tracks),
        "tracks": tracks
    }


@app.post("/api/split-equal")
async def split_by_equal(req: EqualSplitRequest):
    """지정된 곡 수로 시간 균등 분할"""
    count = max(2, min(req.count, 60))
    duration = req.duration
    if not duration or duration < count * 10:
        raise HTTPException(status_code=400, detail="영상 길이가 분할하기에 너무 짧습니다.")
        
    interval = duration / count
    tracks = []
    
    for i in range(count):
        st = round(i * interval, 1)
        et = round((i + 1) * interval, 1) if (i + 1) < count else float(duration)
        idx = i + 1
        ch_id = f"{req.video_id}_eq{idx}"
        ch_title = f"{req.title} - Track {idx:02d}"
        ch_dur = int(et - st)
        
        existing_ch_file = None
        for mp3_path in DOWNLOAD_DIR.glob(f"*{ch_id}*.mp3"):
            existing_ch_file = mp3_path.name
            break

        tracks.append({
            "id": ch_id,
            "parent_id": req.video_id,
            "chapter_index": idx,
            "title": ch_title,
            "uploader": req.uploader,
            "start_time": st,
            "end_time": et,
            "duration": ch_dur,
            "duration_str": format_duration(ch_dur),
            "thumbnail": req.thumbnail,
            "url": req.url,
            "is_downloaded": existing_ch_file is not None,
            "filename": existing_ch_file
        })
        
    return {
        "success": True,
        "count": len(tracks),
        "tracks": tracks
    }


@app.post("/api/split-text")
async def split_by_text(req: TextTimestampSplitRequest):
    """사용자가 붙여넣은 타임스탬프 텍스트로 분할"""
    chapters = parse_description_timestamps(req.text, req.duration)
    if not chapters:
        raise HTTPException(status_code=400, detail="유효한 타임스탬프(예: 00:00 곡제목)를 찾을 수 없습니다.")
        
    tracks = []
    for idx, ch in enumerate(chapters):
        st = ch["start_time"]
        et = ch["end_time"]
        ch_dur = int(et - st)
        ch_id = f"{req.video_id}_t{idx+1}"
        
        existing_ch_file = None
        for mp3_path in DOWNLOAD_DIR.glob(f"*{ch_id}*.mp3"):
            existing_ch_file = mp3_path.name
            break

        tracks.append({
            "id": ch_id,
            "parent_id": req.video_id,
            "chapter_index": idx + 1,
            "title": ch["title"],
            "uploader": req.uploader,
            "start_time": st,
            "end_time": et,
            "duration": ch_dur,
            "duration_str": format_duration(ch_dur),
            "thumbnail": req.thumbnail,
            "url": req.url,
            "is_downloaded": existing_ch_file is not None,
            "filename": existing_ch_file
        })
        
    return {
        "success": True,
        "count": len(tracks),
        "tracks": tracks
    }


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
    decoded_name = urllib.parse.unquote(filename)
    file_path = DOWNLOAD_DIR / decoded_name

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="요청한 파일이 존재하지 않습니다.")

    quoted_name = urllib.parse.quote(file_path.name)
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quoted_name}"
    }

    media_type = "application/zip" if file_path.suffix == ".zip" else "audio/mpeg"
    return FileResponse(path=str(file_path), media_type=media_type, headers=headers)


@app.get("/api/stream/{filename:path}")
async def stream_audio(filename: str):
    decoded_name = urllib.parse.unquote(filename)
    file_path = DOWNLOAD_DIR / decoded_name

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="오디오 파일을 찾을 수 없습니다.")

    return FileResponse(path=str(file_path), media_type="audio/mpeg")


@app.delete("/api/clean-cache")
async def clean_cache():
    deleted_count = 0
    for f in DOWNLOAD_DIR.glob("*.*"):
        if f.name == ".gitkeep":
            continue
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
