# 🎵 기나미 MP3 추출기 (Ginami MP3 Studio)

> 유튜브 단일 영상 및 대용량 재생목록(Playlist)을 최고 음질(320kbps) MP3로 변환하고 다운로드할 수 있는 전문 웹 애플리케이션입니다.

![Preview](static/profile.jpg)

---

## 🌟 주요 기능

- **유튜브 주소 자동 판별**: 단일 동영상, 모바일 공유링크(`youtu.be`), Shorts, 대용량 **재생목록(Playlist)** 지원
- **최고 음질 320kbps 변환**: 원본 오디오 손실을 최소화하는 고음질 인코딩
- **ID3 메타데이터 & 앨범아트 삽입**: 곡 제목, 아티스트명, 고화질 썸네일 커버 사진을 MP3 내부에 자동 주입
- **실시간 진행률 표시**: 다운로드 속도, 남은 시간, 퍼센트(%) 실시간 애니메이션 표시
- **일괄 변환 & ZIP 압축 다운로드**: 플레이리스트의 전 곡을 버튼 하나로 일괄 변환하고 ZIP 압축 파일로 한 번에 다운로드
- **브라우저 즉시 미리듣기**: 다운로드 전 웹 페이지 내에서 즉시 감상 가능한 미니 오디오 플레이어 탑재
- **감성 디자인**: 419 시스템 감성의 포레스트 그린 & 어스 웜톤 UI

---

## 🛠️ 기술 스택

- **Backend**: Python 3.11, FastAPI, Uvicorn
- **Audio Engine**: `yt-dlp`, `FFmpeg`, `Mutagen`
- **Frontend**: HTML5, Tailwind CSS, FontAwesome 6, Google Fonts (고운돋움, Pretendard)

---

## 🚀 빠른 시작 (Getting Started)

### 1. 필수 프로그램 설치
- [Python 3.10+](https://www.python.org/)
- [FFmpeg](https://ffmpeg.org/) (시스템 PATH 등록 필요)

### 2. 의존성 설치
```bash
pip install -r requirements.txt
```

### 3. 서버 실행
```bash
# 윈도우 원클릭 실행
run.bat

# 또는 직접 명령어로 실행
python app.py
```

브라우저에서 `http://127.0.0.1:8088` 로 접속합니다.
