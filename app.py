from flask import Flask, render_template, request, jsonify, send_file, after_this_request
import yt_dlp
import os
import shutil
import threading
import time
import uuid
import zipfile
from urllib.parse import urlparse, urlunparse
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

app = Flask(__name__, template_folder='.', static_folder='.')
CORS(app)

# Create a permanent downloads directory
DL_PATH = os.path.abspath("downloads")
if not os.path.exists(DL_PATH):
    os.makedirs(DL_PATH)

DOWNLOAD_STATUS = {}
STATUS_LOCK = threading.Lock()

def set_download_status(req_id, **kwargs):
    with STATUS_LOCK:
        if req_id not in DOWNLOAD_STATUS:
            DOWNLOAD_STATUS[req_id] = {}
        DOWNLOAD_STATUS[req_id].update(kwargs)


def get_download_status(req_id):
    with STATUS_LOCK:
        return DOWNLOAD_STATUS.get(req_id)


def normalize_soundcloud_url(url):
    if not url:
        return url
    url = url.strip()
    if url.startswith('soundcloud.com'):
        url = 'https://' + url

    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        url = 'https://' + url
        parsed = urlparse(url)

    if parsed.netloc.endswith('soundcloud.com'):
        path = parsed.path.rstrip('/')
        if path and path.count('/') == 1:
            path = path + '/tracks'
            parsed = parsed._replace(path=path)
            url = urlunparse(parsed)

    return url


def normalize_soundcloud_track_url(track_url):
    if not track_url:
        return track_url
    track_url = track_url.strip()
    if track_url.startswith('/'):
        return 'https://soundcloud.com' + track_url
    return track_url

@app.route('/')
def home():
    return render_template('index.html') if os.path.exists('index.html') else "index.html not found"

@app.route('/api/fetch', methods=['POST'])
def fetch_playlist():
    data = request.json
    url = normalize_soundcloud_url(data.get('url'))
    if not url:
        return jsonify({"error": "Please provide a valid SoundCloud URL."}), 400
    
    # Options optimized for SoundCloud metadata
    ydl_opts = {
        'quiet': True,
        'skip_download': True,
        'force_generic_extractor': False,
        'ignoreerrors': True,
    }

    def get_thumbnail(item):
        if not item:
            return ""
        thumb = item.get('thumbnail') or item.get('thumbnail_url') or item.get('artwork_url') or item.get('artwork') or ""
        if not thumb:
            thumbnails = item.get('thumbnails') or []
            for entry in reversed(thumbnails):
                if entry.get('url'):
                    return entry.get('url')
        return thumb or ""

    try:
        is_flat = False
        ydl_opts_flat = {**ydl_opts, 'extract_flat': True}
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            if 'Unable to download JSON metadata' in str(e):
                with yt_dlp.YoutubeDL(ydl_opts_flat) as ydl:
                    info = ydl.extract_info(url, download=False)
                is_flat = True
            else:
                raise

        entries = info.get('entries')
        if entries is None:
            entries = [info]
        elif not is_flat:
            flat_entries = []
            for entry in entries:
                if not entry:
                    continue
                if entry.get('_type') == 'playlist':
                    flat_entries.extend(entry.get('entries', []))
                else:
                    flat_entries.append(entry)
            entries = flat_entries

        if not entries and not is_flat:
            with yt_dlp.YoutubeDL(ydl_opts_flat) as ydl:
                info = ydl.extract_info(url, download=False)
            is_flat = True
            entries = info.get('entries') or []

        playlist_title = (
            info.get('title') or
            info.get('album') or
            info.get('playlist_title') or
            info.get('album_title') or
            (entries[0].get('title') if entries else None) or
            'SoundCloud Uploads'
        )
        playlist_thumb = get_thumbnail(info) or ""

        clean_tracks = []
        if is_flat:
            track_ydl_opts = {**ydl_opts, 'ignoreerrors': True}
            with yt_dlp.YoutubeDL(track_ydl_opts) as track_ydl:
                for entry in entries:
                    if not entry:
                        continue
                    track_url = entry.get('webpage_url') or entry.get('original_url') or entry.get('url')
                    track_url = normalize_soundcloud_track_url(track_url)
                    if not track_url:
                        continue

                    track_info = None
                    try:
                        track_info = track_ydl.extract_info(track_url, download=False)
                    except Exception:
                        pass

                    title = 'Unknown Track'
                    uploader = 'Unknown Artist'
                    thumb = playlist_thumb
                    real_url = track_url

                    if track_info:
                        title = track_info.get('title') or track_info.get('track') or title
                        uploader = track_info.get('uploader') or track_info.get('user', {}).get('username') or uploader
                        thumb = get_thumbnail(track_info) or thumb
                        real_url = track_info.get('webpage_url') or track_info.get('original_url') or track_info.get('url') or track_url
                    else:
                        title = entry.get('title') or entry.get('track') or title
                        uploader = entry.get('uploader') or entry.get('uploader_id') or entry.get('uploader_url') or uploader
                        thumb = get_thumbnail(entry) or thumb

                    clean_tracks.append({
                        'title': title,
                        'uploader': uploader,
                        'url': real_url,
                        'thumbnail': thumb
                    })
        else:
            for entry in entries:
                if not entry:
                    continue

                title = entry.get('title') or entry.get('track') or 'Unknown Track'
                uploader = entry.get('uploader') or entry.get('user', {}).get('username') or 'Unknown Artist'
                thumb = get_thumbnail(entry)
                track_url = entry.get('webpage_url') or entry.get('original_url') or entry.get('url')

                if not track_url:
                    continue

                clean_tracks.append({
                    'title': title,
                    'uploader': uploader,
                    'url': track_url,
                    'thumbnail': thumb
                })

        if not playlist_thumb and clean_tracks:
            playlist_thumb = clean_tracks[0].get('thumbnail') or playlist_thumb

        if not clean_tracks:
            return jsonify({"error": "No playable SoundCloud tracks found."}), 400

        return jsonify({
            "entries": clean_tracks,
            "title": playlist_title,
            "thumbnail": playlist_thumb
        })
    except Exception as e:
        app.logger.exception("SoundCloud fetch failed for URL: %s", url)
        message = str(e)
        if 'Unable to download JSON metadata' in message:
            message = f"SoundCloud metadata could not be resolved: {message}. Check that the URL is a public track, playlist, or user tracks page."
        return jsonify({"error": message}), 400

@app.route('/api/download-zip', methods=['POST'])
def download_zip():
    data = request.json
    tracks = [t for t in (data.get('tracks') or []) if t and t.get('url')]
    if not tracks:
        return jsonify({"error": "No valid track URLs provided."}), 400

    urls = [normalize_soundcloud_track_url(t.get('url')) for t in tracks]
    p_name = "".join([c for c in data.get('name', 'Playlist') if c.isalnum() or c in (' ', '_')]).strip() or 'Playlist'
    req_id = str(uuid.uuid4())
    temp_dir = os.path.join(DL_PATH, req_id)
    zip_path = os.path.join(DL_PATH, f"{req_id}.zip")

    set_download_status(req_id,
                        status='queued',
                        stage='queued',
                        message='Waiting to start download',
                        total=len(urls),
                        completed=0,
                        current_track_title='',
                        percent=0,
                        eta_text='Estimating...',
                        download_ready=False,
                        error=None,
                        name=p_name)

    def build_zip():
        start_time = time.time()
        total = len(urls)
        os.makedirs(temp_dir, exist_ok=True)
        ydl_opts = {
            'format': 'bestaudio/best',
            'restrictfilenames': True,
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'noplaylist': True,
            'continuedl': True,
            'retries': 3,
            # request thumbnails and metadata so we can embed cover art
            'writethumbnail': True,
            'addmetadata': True,
            # don't set a global outtmpl here — we'll set a per-track outtmpl to include index
        }

        try:
            set_download_status(req_id, stage='downloading', message='Downloading tracks', percent=5)
            for index, track in enumerate(tracks, start=1):
                track_url = normalize_soundcloud_track_url(track.get('url'))
                track_title = track.get('title') or track_url
                set_download_status(req_id,
                                    stage='downloading',
                                    message=f'Downloading track {index} of {total}',
                                    current_track_title=track_title,
                                    completed=index - 1,
                                    percent=max(5, int((index - 1) / total * 80)))

                per_track_opts = dict(ydl_opts)
                safe_index = f"{index:03d}"
                per_track_opts['outtmpl'] = os.path.join(temp_dir, f"{safe_index} - %(title)s.%(ext)s")

                # If ffmpeg is available, extract to high-quality MP3 and embed thumbnail/metadata
                if shutil.which('ffmpeg'):
                    per_track_opts['postprocessors'] = [
                        {
                            'key': 'FFmpegExtractAudio',
                            'preferredcodec': 'mp3',
                            'preferredquality': '320',
                        },
                        {'key': 'EmbedThumbnail'},
                        {'key': 'FFmpegMetadata'},
                    ]
                else:
                    # Try to at least embed metadata where possible and keep original audio
                    per_track_opts['postprocessors'] = [{'key': 'FFmpegMetadata'}]

                with yt_dlp.YoutubeDL(per_track_opts) as ydl:
                    try:
                        ydl.download([track_url])
                    except Exception as e:
                        app.logger.warning('Failed to download track %s: %s', track_url, e)

                elapsed = time.time() - start_time
                remaining = int((elapsed / index) * (total - index)) if index else None
                set_download_status(req_id,
                                    completed=index,
                                    percent=min(90, int((index / total) * 90)),
                                    eta_text=f'{remaining}s' if remaining is not None else 'Estimating...')

            set_download_status(req_id, stage='zipping', message='Creating ZIP archive', percent=95, current_track_title='')
            files = [f for f in os.listdir(temp_dir) if os.path.isfile(os.path.join(temp_dir, f))]
            if not files:
                raise RuntimeError('No files were downloaded.')

            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
                for f in files:
                    z.write(os.path.join(temp_dir, f), f)

            shutil.rmtree(temp_dir)
            set_download_status(req_id,
                                stage='ready',
                                message='Ready to download',
                                percent=100,
                                download_ready=True,
                                eta_text='0s')
        except Exception as e:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            set_download_status(req_id,
                                stage='error',
                                message=str(e),
                                error=str(e),
                                percent=100,
                                download_ready=False)

    thread = threading.Thread(target=build_zip, daemon=True)
    thread.start()

    return jsonify({
        'id': req_id,
        'status_url': f'/api/download-status/{req_id}',
        'download_url': f'/api/download-result/{req_id}'
    })


@app.route('/api/download-status/<string:req_id>', methods=['GET'])
def download_status(req_id):
    status = get_download_status(req_id)
    if not status:
        return jsonify({"error": "Download ID not found."}), 404
    return jsonify(status)


@app.route('/api/download-result/<string:req_id>', methods=['GET'])
def download_result(req_id):
    status = get_download_status(req_id)
    if not status:
        return jsonify({"error": "Download ID not found."}), 404
    if not status.get('download_ready'):
        return jsonify({"error": "Download is not ready yet."}), 400

    file_path = os.path.join(DL_PATH, f"{req_id}.zip")
    if not os.path.exists(file_path):
        return jsonify({"error": "ZIP file is missing."}), 404

    @after_this_request
    def cleanup(response):
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
        with STATUS_LOCK:
            DOWNLOAD_STATUS.pop(req_id, None)
        return response

    return send_file(file_path, as_attachment=True, download_name=f"{status.get('name', 'Playlist')}.zip")


@app.errorhandler(Exception)
def handle_all_exceptions(e):
    app.logger.exception('Unhandled exception: %s', e)
    if isinstance(e, HTTPException):
        return jsonify({"error": e.description}), e.code
    return jsonify({"error": str(e) or 'Internal server error'}), 500


if __name__ == '__main__':
    import sys
    # Check if running on Render or in production
    is_production = os.environ.get('RENDER') == 'true' or os.environ.get('FLASK_ENV') == 'production'
    port = int(os.environ.get('PORT', 5000))
    
    if is_production:
        # Production mode: bind to 0.0.0.0, disable debug
        app.run(host='0.0.0.0', port=port, debug=False)
    else:
        # Development mode: localhost with debug
        app.run(host='127.0.0.1', port=port, debug=True)
