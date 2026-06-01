"""
🤖 Universal Reddit Scraper Suite
Full-featured scraper with analytics, dashboard, notifications, and scheduling.
"""
import requests
import pandas as pd
import datetime
import time
import os
import defusedxml.ElementTree as ET
import argparse
import random
import sys
import json
import subprocess
import tempfile
from urllib.parse import urlparse
from pathlib import Path

# Suppress HTTPS proxy ssl verification warnings from urllib3
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Prevent Unicode encoding issues in Windows console
import sys
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

# Load environment variables from .env file if it exists
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip('"').strip("'")

# --- CONFIGURATION ---
from config import (
    USER_AGENT, MIRRORS,
    REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME,
    REDDIT_PASSWORD, REDDIT_USER_AGENT,
)

PROXY_URL = os.getenv("PROXY_URL", "")
PROXY_TO_USE = ""

# Reddit official API (OAuth) is used when client credentials are configured.
USE_REDDIT_OAUTH = bool(REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET)


def ensure_oauth_header(force=False):
    """Fetch/refresh the Reddit bearer token and set it on the session."""
    from scraper import reddit_oauth
    token = reddit_oauth.get_token(
        REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET,
        REDDIT_USERNAME, REDDIT_PASSWORD, REDDIT_USER_AGENT, force=force,
    )
    SESSION.headers["Authorization"] = f"bearer {token}"


def respect_rate_limit(response):
    """Proactively stay within Reddit's OAuth rate limit.

    oauth.reddit.com returns X-Ratelimit-Remaining / -Reset on every response.
    When the remaining budget for the current window is nearly exhausted, sleep
    until the window resets so we never exceed the limit (or trigger a 429).
    """
    try:
        remaining = float(response.headers.get("x-ratelimit-remaining", "100"))
        reset = float(response.headers.get("x-ratelimit-reset", "0"))
    except (TypeError, ValueError):
        return
    if remaining <= 2 and reset > 0:
        print(f"   ⏳ Reddit rate limit nearly reached ({remaining:.0f} left) - pausing {reset:.0f}s")
        time.sleep(reset + 1)

def rotate_session_proxy(country=None, session_id=None, force_rotate=False):
    """Dynamically rotates/updates the proxy URL on the global SESSION object."""
    global PROXY_TO_USE
    if not PROXY_TO_USE or PROXY_TO_USE.lower() in ["none", "direct", "disabled", ""]:
        return
    
    try:
        from config import get_formatted_proxy_url
        formatted_url = get_formatted_proxy_url(PROXY_TO_USE, country, session_id, force_rotate)
        SESSION.proxies = {
            "http": formatted_url,
            "https": formatted_url,
        }
        os.environ["HTTP_PROXY"] = formatted_url
        os.environ["HTTPS_PROXY"] = formatted_url
    except Exception as e:
        print(f"⚠️ Error rotating proxy: {e}")

SEEN_URLS = set()
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

def request_with_retry(url, retries=3, backoff=2, **kwargs):
    """Makes a GET request with retry logic, especially useful for rotating proxies."""
    for attempt in range(retries):
        try:
            if USE_REDDIT_OAUTH:
                ensure_oauth_header()
            else:
                rotate_session_proxy(force_rotate=True)
            response = SESSION.get(url, **kwargs)
            if USE_REDDIT_OAUTH:
                respect_rate_limit(response)
            if response.status_code in (401, 403) and USE_REDDIT_OAUTH and attempt < retries - 1:
                # Token may be expired/invalid - force a refresh and retry.
                ensure_oauth_header(force=True)
                raise requests.exceptions.RequestException(
                    f"OAuth {response.status_code}, refreshed token"
                )
            if response.status_code == 200:
                content_type = response.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    return response
                elif ".json" in url and "text/html" in content_type:
                    if "Making sure you're not a bot" in response.text or "bot check" in response.text.lower():
                        raise requests.exceptions.RequestException("Bot challenge detected on mirror")
                return response
            elif response.status_code == 429:
                # Honor Reddit's Retry-After / rate-limit reset if provided.
                wait = backoff * (attempt + 1)
                for h in ("retry-after", "x-ratelimit-reset"):
                    try:
                        wait = max(wait, float(response.headers.get(h, 0)))
                    except (TypeError, ValueError):
                        pass
                print(f"   ⏳ 429 rate limited - waiting {wait:.0f}s")
                time.sleep(wait)
            else:
                raise requests.exceptions.HTTPError(f"HTTP {response.status_code}")
        except Exception as e:
            if attempt < retries - 1:
                print(f"   ⚠️ Request failed: {e}. Retrying ({attempt + 2}/{retries})...")
                time.sleep(backoff)
            else:
                raise e

# --- DIRECTORY SETUP ---
def setup_directories(target, prefix):
    """Creates organized folder structure for scraped data."""
    base_dir = f"data/{prefix}_{target}"
    dirs = {
        "base": base_dir,
        "posts": f"{base_dir}/posts.csv",
        "comments": f"{base_dir}/comments.csv",
        "media": f"{base_dir}/media",
        "images": f"{base_dir}/media/images",
        "videos": f"{base_dir}/media/videos",
    }
    # Directories are created lazily by download_post_media only when media is
    # actually saved, so DB-only (no-media) scrapes leave no folders behind.
    return dirs

def get_file_path(target, type_prefix):
    """Legacy function for backward compatibility."""
    if not os.path.exists("data"):
        os.makedirs("data")
    sanitized_target = target.replace("/", "_")
    return f"data/{type_prefix}_{sanitized_target}.csv"

def load_history(filepath):
    """Loads existing CSV history to prevent duplicates."""
    SEEN_URLS.clear()
    if os.path.exists(filepath):
        try:
            df = pd.read_csv(filepath)
            for url in df['permalink']:
                SEEN_URLS.add(str(url))
            print(f"📚 Loaded {len(SEEN_URLS)} existing items from {filepath}")
        except:
            pass

def save_posts_csv(posts, filepath):
    """Saves posts to CSV with all metadata."""
    if not posts:
        return 0
    
    new_posts = [p for p in posts if p['permalink'] not in SEEN_URLS]
    
    if new_posts:
        df = pd.DataFrame(new_posts)
        if os.path.exists(filepath):
            df.to_csv(filepath, mode='a', header=False, index=False)
        else:
            df.to_csv(filepath, index=False)
        
        for p in new_posts:
            SEEN_URLS.add(p['permalink'])
        
        print(f"✅ Saved {len(new_posts)} new posts")
        return len(new_posts)
    else:
        print("💤 No new unique posts found.")
        return 0

def save_comments_csv(comments, filepath):
    """Saves comments to CSV."""
    if not comments:
        return
    
    df = pd.DataFrame(comments)
    if os.path.exists(filepath):
        df.to_csv(filepath, mode='a', header=False, index=False)
    else:
        df.to_csv(filepath, index=False)
    
    print(f"💬 Saved {len(comments)} comments")

# --- MEDIA DOWNLOAD ---
def get_media_urls(post_data):
    """Extracts all media URLs from a post."""
    media = {"images": [], "videos": [], "galleries": []}
    
    url = post_data.get('url', '')
    if any(ext in url.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
        media["images"].append(url)
    
    if 'i.redd.it' in url:
        media["images"].append(url)
    
    if post_data.get('is_video'):
        reddit_video = post_data.get('media', {})
        if reddit_video and 'reddit_video' in reddit_video:
            video_url = reddit_video['reddit_video'].get('fallback_url', '')
            if video_url:
                media["videos"].append(video_url.split('?')[0])
    
    preview = post_data.get('preview', {})
    if preview and 'images' in preview:
        for img in preview['images']:
            source = img.get('source', {})
            if source.get('url'):
                clean_url = source['url'].replace('&amp;', '&')
                media["images"].append(clean_url)
    
    if post_data.get('is_gallery'):
        gallery_data = post_data.get('gallery_data', {})
        media_metadata = post_data.get('media_metadata', {})
        
        if gallery_data and media_metadata:
            for item in gallery_data.get('items', []):
                media_id = item.get('media_id')
                if media_id and media_id in media_metadata:
                    meta = media_metadata[media_id]
                    if meta.get('s', {}).get('u'):
                        clean_url = meta['s']['u'].replace('&amp;', '&')
                        media["galleries"].append(clean_url)
    
    if 'youtube.com' in url or 'youtu.be' in url:
        media["videos"].append(url)
    
    return media

def download_media(url, save_path, media_type="image"):
    """Downloads a single media file."""
    try:
        if os.path.exists(save_path):
            return True
        
        response = request_with_retry(url, timeout=30, stream=True)
        if response.status_code == 200:
            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
    except Exception as e:
        pass
    return False

def download_reddit_video_with_audio(video_url, save_path):
    """
    Downloads Reddit video with audio by fetching both streams and merging.
    Reddit stores video and audio separately - this combines them.
    """
    try:
        if os.path.exists(save_path):
            return True
        
        # Try to find the audio URL by replacing video quality with audio
        # Reddit videos have audio at URLs like .../DASH_audio.mp4 or .../DASH_AUDIO_128.mp4
        base_url = video_url.rsplit('/', 1)[0]
        
        # Common audio URL patterns
        audio_urls = [
            f"{base_url}/DASH_audio.mp4",
            f"{base_url}/DASH_AUDIO_128.mp4",
            f"{base_url}/DASH_AUDIO_64.mp4",
            f"{base_url}/audio.mp4",
            f"{base_url}/audio"
        ]
        
        # Download video to temp file first
        with tempfile.NamedTemporaryFile(suffix='_video.mp4', delete=False) as video_temp:
            video_temp_path = video_temp.name
            response = request_with_retry(video_url, timeout=60, stream=True)
            if response.status_code != 200:
                return False
            for chunk in response.iter_content(chunk_size=8192):
                video_temp.write(chunk)
        
        # Try to download audio
        audio_temp_path = None
        for audio_url in audio_urls:
            try:
                response = request_with_retry(audio_url, timeout=30, stream=True)
                if response.status_code == 200:
                    with tempfile.NamedTemporaryFile(suffix='_audio.mp4', delete=False) as audio_temp:
                        audio_temp_path = audio_temp.name
                        for chunk in response.iter_content(chunk_size=8192):
                            audio_temp.write(chunk)
                    break
            except:
                continue
        
        if audio_temp_path:
            # Merge video and audio using ffmpeg
            try:
                cmd = [
                    'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                    '-i', video_temp_path,
                    '-i', audio_temp_path,
                    '-c:v', 'copy', '-c:a', 'aac',
                    '-shortest', save_path
                ]
                result = subprocess.run(cmd, capture_output=True, timeout=120)
                
                if result.returncode == 0:
                    # Cleanup temp files
                    os.unlink(video_temp_path)
                    os.unlink(audio_temp_path)
                    return True
                else:
                    # ffmpeg failed, fall back to video only
                    print(f"   ⚠️ ffmpeg merge failed, saving video without audio")
                    os.rename(video_temp_path, save_path)
                    os.unlink(audio_temp_path)
                    return True
            except FileNotFoundError:
                # ffmpeg not installed, save video only
                print(f"   ⚠️ ffmpeg not found, saving video without audio")
                os.rename(video_temp_path, save_path)
                if audio_temp_path:
                    os.unlink(audio_temp_path)
                return True
            except Exception as e:
                # Other error, save video only
                os.rename(video_temp_path, save_path)
                if audio_temp_path and os.path.exists(audio_temp_path):
                    os.unlink(audio_temp_path)
                return True
        else:
            # No audio found, just use video
            os.rename(video_temp_path, save_path)
            return True
            
    except Exception as e:
        # Cleanup any temp files on error
        pass
    return False

def download_post_media(post_data, dirs, post_id):
    """Downloads all media from a post."""
    media = get_media_urls(post_data)
    downloaded = {"images": 0, "videos": 0}

    # Create media directories on demand (they aren't pre-created any more).
    os.makedirs(dirs["images"], exist_ok=True)
    os.makedirs(dirs["videos"], exist_ok=True)

    for i, img_url in enumerate(media["images"][:5]):
        ext = os.path.splitext(urlparse(img_url).path)[1] or '.jpg'
        save_path = os.path.join(dirs["images"], f"{post_id}_{i}{ext}")
        if download_media(img_url, save_path, "image"):
            downloaded["images"] += 1
    
    for i, img_url in enumerate(media["galleries"][:10]):
        ext = '.jpg'
        save_path = os.path.join(dirs["images"], f"{post_id}_gallery_{i}{ext}")
        if download_media(img_url, save_path, "gallery"):
            downloaded["images"] += 1
    
    for i, vid_url in enumerate(media["videos"][:2]):
        if 'youtube' not in vid_url:
            ext = '.mp4'
            save_path = os.path.join(dirs["videos"], f"{post_id}_{i}{ext}")
            # Use enhanced download for Reddit videos (includes audio)
            if 'v.redd.it' in vid_url or 'reddit.com' in vid_url:
                if download_reddit_video_with_audio(vid_url, save_path):
                    downloaded["videos"] += 1
            elif download_media(vid_url, save_path, "video"):
                downloaded["videos"] += 1
    
    return downloaded

# --- COMMENT SCRAPING ---
# Age-aware comment retrieval policy. New posts are still gaining replies, so we grab
# the full tree; older threads are scored by the community, so we switch to the most
# up-voted comments (upvotes matter more than reply count once a thread matures).
NEW_POST_WINDOW_HOURS = 48      # "new" = full comment tree, every reply, no score floor
NEW_MAX_DEPTH = 10              # effectively the whole tree for these subs
MATURE_MAX_DEPTH = 3            # mature threads: shallower
MATURE_COMMENT_SCORE_MIN = 5   # mature threads: keep only comments with >= this upvotes
COMMENT_REFRESH_DAYS = 14       # how far back the smart refresh revisits stored threads


def comment_fetch_policy(created_utc_iso):
    """Pick (sort, max_depth, score_min) for a post based on its age.

    New (<=48h): full tree, all replies, no upvote floor.
    Mature (>48h): Reddit's top-sorted comments, keep only those with >=5 upvotes.
    """
    try:
        age_h = (datetime.datetime.now()
                 - datetime.datetime.fromisoformat(created_utc_iso)).total_seconds() / 3600
    except (ValueError, TypeError):
        age_h = float("inf")
    if age_h <= NEW_POST_WINDOW_HOURS:
        return (None, NEW_MAX_DEPTH, 0)
    return ("top", MATURE_MAX_DEPTH, MATURE_COMMENT_SCORE_MIN)


def scrape_comments(permalink, max_depth=3, sort=None, score_min=0):
    """Scrapes comments from a post.

    sort: Reddit comment sort ('top', 'best', 'new'...) or None for the default.
    score_min: drop comments below this upvote count (replies are still traversed,
        so a high-scoring reply under a low-scoring parent is still captured).
    """
    comments = []
    sort_q = f"&sort={sort}" if sort else ""

    try:
        if USE_REDDIT_OAUTH:
            # permalink is a path like /r/sub/comments/id/title/ ; use the OAuth host.
            path = urlparse(permalink).path if permalink.startswith('http') else permalink
            url = f"https://oauth.reddit.com{path}?limit=100{sort_q}"
        elif not permalink.startswith('http'):
            url = f"https://old.reddit.com{permalink}.json?limit=100{sort_q}"
        else:
            url = f"{permalink}.json?limit=100{sort_q}"

        response = request_with_retry(url, timeout=15)
        if response.status_code != 200:
            return comments

        data = response.json()

        if len(data) > 1:
            comment_data = data[1]['data']['children']
            comments = parse_comments(comment_data, permalink, depth=0,
                                      max_depth=max_depth, score_min=score_min)

    except Exception as e:
        pass

    if len(comments) > 0:
        print(f"   + Scraped {len(comments)} comments")

    return comments

def parse_comments(comment_list, post_permalink, depth=0, max_depth=3, score_min=0):
    """Recursively parses comments, keeping those scoring >= score_min."""
    comments = []

    if depth > max_depth:
        return comments

    for item in comment_list:
        if item['kind'] != 't1':
            continue

        c = item['data']

        comment = {
            "post_permalink": post_permalink,
            "comment_id": c.get('id'),
            "parent_id": c.get('parent_id'),
            "author": c.get('author'),
            "body": c.get('body', ''),
            "score": c.get('score', 0),
            "created_utc": datetime.datetime.fromtimestamp(c.get('created_utc', 0)).isoformat(),
            "depth": depth,
            "is_submitter": c.get('is_submitter', False),
        }
        # Upvote floor for mature threads; replies are still traversed below so a
        # popular reply under an unpopular parent is not lost.
        if comment["score"] >= score_min:
            comments.append(comment)

        replies = c.get('replies')
        if replies and isinstance(replies, dict):
            reply_children = replies.get('data', {}).get('children', [])
            comments.extend(parse_comments(reply_children, post_permalink, depth + 1,
                                           max_depth, score_min))

    return comments


def fetch_post_meta_batch(post_ids):
    """Current num_comments + score for up to 100 posts via Reddit's /api/info.

    One cheap request covers 100 stored threads, so we can detect which gained
    comments without re-fetching every thread.
    """
    if not post_ids:
        return {}
    fullnames = ",".join(f"t3_{pid}" for pid in post_ids[:100])
    if USE_REDDIT_OAUTH:
        url = f"https://oauth.reddit.com/api/info?id={fullnames}&raw_json=1"
    else:
        url = f"https://www.reddit.com/api/info.json?id={fullnames}&raw_json=1"
    meta = {}
    try:
        resp = request_with_retry(url, timeout=15)
        if resp.status_code == 200:
            for child in resp.json().get('data', {}).get('children', []):
                d = child.get('data', {})
                pid = d.get('id')
                if pid:
                    meta[pid] = {"num_comments": d.get('num_comments', 0),
                                 "score": d.get('score', 0)}
    except Exception as e:
        print(f"   ⚠️ /api/info failed: {e}")
    return meta


def _fetch_one_author_age(username):
    """Fetch + store one author's account metadata AND lifecycle status. Distinguishes
    active vs suspended (Reddit-banned: 200 + is_suspended) vs deleted (404). Conservative:
    transient errors (401/403/429/5xx/network) record 'unknown', never a false 'gone'."""
    from export.database import save_author, set_author_status
    url = f"https://oauth.reddit.com/user/{username}/about"
    try:
        if USE_REDDIT_OAUTH:
            ensure_oauth_header()
        resp = SESSION.get(url, timeout=15)
        if USE_REDDIT_OAUTH:
            respect_rate_limit(resp)
        sc = resp.status_code
        if sc == 200:
            d = resp.json().get("data", {}) or {}
            if d.get("is_suspended"):
                set_author_status(username, "suspended")  # Reddit-banned: strongest tell
                return
            cu = d.get("created_utc")
            if cu:
                save_author(
                    username, datetime.datetime.fromtimestamp(cu).isoformat(),
                    round((time.time() - cu) / 86400, 1),
                    comment_karma=d.get("comment_karma"), link_karma=d.get("link_karma"),
                    total_karma=d.get("total_karma"), awardee_karma=d.get("awardee_karma"),
                    is_mod=int(bool(d.get("is_mod"))), is_gold=int(bool(d.get("is_gold"))),
                    is_employee=int(bool(d.get("is_employee"))),
                    has_verified_email=int(bool(d.get("has_verified_email"))),
                    verified=int(bool(d.get("verified"))),
                )
                set_author_status(username, "active")
                return
            set_author_status(username, "unknown")  # 200 but odd shape
            return
        if sc == 404:
            set_author_status(username, "deleted")   # self-deleted / never existed
            return
        if sc == 401 and USE_REDDIT_OAUTH:
            try:
                ensure_oauth_header(force=True)       # token expired mid-sweep; recheck later
            except Exception:
                pass
    except Exception:
        pass
    set_author_status(username, "unknown")            # transient - leave for next pass


def ensure_author_ages(authors):
    """Fetch ages for any of these authors not already recorded. Called inline when
    new authors are discovered during scraping (cached -> each author fetched once)."""
    if not USE_REDDIT_OAUTH:
        return 0
    from export.database import get_author_ages
    cand = {a for a in authors if a and a not in ("[deleted]", "AutoModerator")}
    if not cand:
        return 0
    have = set(get_author_ages(cand).keys())  # already recorded (incl. null-age 404s)
    todo = cand - have
    for u in todo:
        _fetch_one_author_age(u)
    return len(todo)


def author_age_backfill(max_authors=2000):
    """Safety-net pass: fetch account age for any discovered author still missing one."""
    from export.database import get_authors_needing_age
    done = 0
    while done < max_authors:
        batch = get_authors_needing_age(limit=100)
        if not batch:
            break
        for u in batch:
            _fetch_one_author_age(u)
            done += 1
        print(f"   👤 author ages fetched: {done}")
    return {"fetched": done}


def author_status_revalidate(max_authors=500, stale_days=14):
    """Re-check lifecycle status for stale/active authors, catching accounts deleted or
    suspended AFTER we scraped them - the longitudinal half of the throwaway-pump signal
    (active when they hyped a ticker, gone later). Prioritizes recently-active authors."""
    if not USE_REDDIT_OAUTH:
        return 0
    from export.database import get_authors_needing_status_recheck
    todo = get_authors_needing_status_recheck(limit=max_authors, stale_days=stale_days)
    for u in todo:
        _fetch_one_author_age(u)
    if todo:
        print(f"   🔁 Author status revalidated: {len(todo)}")
    return len(todo)


def _scheduler_heartbeat():
    """Refresh the scheduler heartbeat from inside long-running work, so a long
    update cycle doesn't look stale to the health check / scheduler_status tool.
    No-op outside the scheduler (best-effort)."""
    try:
        from scheduler import control as _c
        _c.write_status(heartbeat=_c.now_iso(), heartbeat_epoch=time.time())
    except Exception:
        pass


def refresh_existing_comments(target, since_days=COMMENT_REFRESH_DAYS, dry_run=False):
    """Smart comment refresh for already-stored threads (new comments on existing posts).

    For stored posts in the last `since_days`, batch-check Reddit's current comment
    count; re-fetch + upsert comments only for threads that gained comments (or that we
    never fetched). Retrieval depth/filter follows the age policy: full tree for posts
    still inside the new window, top up-voted comments (>=5) once mature.
    """
    from export.database import (get_posts_for_comment_refresh, save_comments_batch,
                                 update_post_counts)
    posts = get_posts_for_comment_refresh(target, since_days)
    if not posts:
        return {"checked": 0, "refreshed": 0, "new_comments": 0}

    by_id = {p['id']: p for p in posts}
    ids = list(by_id.keys())
    refreshed = 0
    new_comment_rows = 0

    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        _scheduler_heartbeat()  # keep heartbeat fresh across a long refresh
        meta = fetch_post_meta_batch(chunk)
        for pid in chunk:
            p = by_id[pid]
            m = meta.get(pid)
            if not m:
                continue
            current = m["num_comments"]
            grew = current > (p.get("stored_num_comments") or 0)
            missing = current > 0 and (p.get("stored_rows") or 0) == 0
            if not (grew or missing):
                continue  # unchanged thread — skip the expensive comment fetch

            sort, depth, cmin = comment_fetch_policy(p.get("created_utc"))
            comments = scrape_comments(p["permalink"], max_depth=depth,
                                       sort=sort, score_min=cmin)
            if comments and not dry_run:
                try:
                    from analytics.enrich import enrich_comments
                    from export.database import save_ticker_mentions
                    c_mentions = enrich_comments(comments, target)
                    save_comments_batch(comments, pid, upsert=True)
                    save_ticker_mentions(c_mentions)
                    new_comment_rows += len(comments)
                except Exception:
                    pass
            if not dry_run:
                update_post_counts(pid, current, m.get("score"))
            refreshed += 1
            time.sleep(1)  # be polite to Reddit
            _scheduler_heartbeat()  # bound heartbeat staleness to one refetch

    print(f"   🔁 Comment refresh r/{target}: checked {len(ids)} threads, "
          f"refreshed {refreshed}, +{new_comment_rows} comment rows")
    return {"checked": len(ids), "refreshed": refreshed, "new_comments": new_comment_rows}

# --- POST EXTRACTION ---
def extract_post_data(post_json):
    """Extracts comprehensive post data."""
    p = post_json
    
    post_type = "text"
    if p.get('is_video'):
        post_type = "video"
    elif p.get('is_gallery'):
        post_type = "gallery"
    elif any(ext in p.get('url', '').lower() for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']) or 'i.redd.it' in p.get('url', ''):
        post_type = "image"
    elif p.get('is_self'):
        post_type = "text"
    else:
        post_type = "link"
    
    return {
        "id": p.get('id'),
        "title": p.get('title'),
        "author": p.get('author'),
        "created_utc": datetime.datetime.fromtimestamp(p.get('created_utc', 0)).isoformat(),
        "permalink": p.get('permalink'),
        "url": p.get('url_overridden_by_dest', p.get('url')),
        "score": p.get('score', 0),
        "upvote_ratio": p.get('upvote_ratio', 0),
        "num_comments": p.get('num_comments', 0),
        "num_crossposts": p.get('num_crossposts', 0),
        "selftext": p.get('selftext', ''),
        "post_type": post_type,
        "is_nsfw": p.get('over_18', False),
        "is_spoiler": p.get('spoiler', False),
        "flair": p.get('link_flair_text', ''),
        "total_awards": p.get('total_awards_received', 0),
        "has_media": p.get('is_video', False) or p.get('is_gallery', False) or 'i.redd.it' in p.get('url', ''),
        "media_downloaded": False,
        "source": "History-Full"
    }

# --- FULL HISTORY SCRAPE ---
def run_full_history(target, limit, is_user=False, download_media_flag=True,
                     scrape_comments_flag=True, dry_run=False, use_plugins=False,
                     max_age_days=None, comment_score_min=0, refresh=False):
    """
    Full scrape with images, videos, and comments.
    
    Args:
        target: Subreddit or username
        limit: Maximum posts to scrape
        is_user: True if target is a user
        download_media_flag: Download images/videos
        scrape_comments_flag: Scrape comments
        dry_run: Simulate without saving data
        use_plugins: Run post-processing plugins
    """
    prefix = "u" if is_user else "r"
    mode = "full" if download_media_flag and scrape_comments_flag else "history"
    
    # Display mode banner
    if dry_run:
        print("=" * 50)
        print("🧪 DRY RUN MODE - No data will be saved")
        print("=" * 50)
    
    print(f"🚀 Starting {'DRY RUN' if dry_run else 'FULL HISTORY'} scrape for {prefix}/{target}")
    print(f"   📊 Target posts: {limit}")
    print(f"   🖼️  Download media: {download_media_flag and not dry_run}")
    print(f"   💬 Scrape comments: {scrape_comments_flag}")
    print(f"   🔌 Plugins enabled: {use_plugins}")
    print("-" * 50)
    
    # Start job tracking
    job_id = None
    try:
        from export.database import start_job_record, complete_job_record
        job_id = start_job_record(target, mode, is_user, dry_run)
    except Exception as e:
        print(f"⚠️ Job tracking unavailable: {e}")
    
    # Setup directories (media is written lazily; posts/comments go to the DB)
    dirs = setup_directories(target, prefix)
    # Seed the dedup set from the DB (single source of truth).
    SEEN_URLS.clear()
    try:
        from export.database import get_post_permalinks
        SEEN_URLS.update(get_post_permalinks(target))
        if SEEN_URLS:
            print(f"📚 Loaded {len(SEEN_URLS)} existing items from database")
    except Exception as e:
        print(f"⚠️ Could not load history from DB: {e}")

    after = None
    total_posts = 0
    total_media = {"images": 0, "videos": 0}
    total_comments = 0
    all_scraped_posts = []  # For plugin processing
    all_scraped_comments = []
    start_time = time.time()
    error_msg = None

    # Optional time window: stop paginating once posts are older than the cutoff
    # (the /new listing is reverse-chronological, so this bounds the backfill).
    cutoff_dt = None
    reached_cutoff = False
    if max_age_days:
        cutoff_dt = datetime.datetime.now() - datetime.timedelta(days=max_age_days)
        print(f"   🗓️  Time window: posts newer than {cutoff_dt.date()} ({max_age_days}d)")

    try:
        while total_posts < limit and not reached_cutoff:
            random.shuffle(MIRRORS)
            success = False
            
            for base_url in MIRRORS:
                try:
                    # oauth.reddit.com listing endpoints don't use the .json suffix.
                    suffix = "" if USE_REDDIT_OAUTH else ".json"
                    if is_user:
                        path = f"/user/{target}/submitted{suffix}"
                    else:
                        path = f"/r/{target}/new{suffix}"
                    
                    # Use proper batch size - min of remaining posts needed or 100 (Reddit's max per request)
                    batch_size = min(100, limit - total_posts)
                    target_url = f"{base_url}{path}?limit={batch_size}&raw_json=1"
                    if after:
                        target_url += f"&after={after}"
                    
                    print(f"\n📡 Fetching from: {base_url}")
                    response = request_with_retry(target_url, timeout=15)
                    
                    if response.status_code == 200:
                        data = response.json()
                        posts = []
                        batch_comments = []
                        
                        children = data['data']['children']
                        print(f"   Found {len(children)} posts in this batch")
                        
                        for child in children:
                            p = child['data']
                            post = extract_post_data(p)

                            # Stop once we pass the time window (reverse-chronological feed).
                            if cutoff_dt:
                                try:
                                    if datetime.datetime.fromisoformat(post['created_utc']) < cutoff_dt:
                                        reached_cutoff = True
                                        break
                                except (ValueError, TypeError):
                                    pass

                            # In refresh mode, re-process seen posts to update them.
                            if not refresh and post['permalink'] in SEEN_URLS:
                                continue

                            # Download media (skip in dry run)
                            if download_media_flag and not dry_run:
                                downloaded = download_post_media(p, dirs, post['id'])
                                post['media_downloaded'] = downloaded['images'] > 0 or downloaded['videos'] > 0
                                total_media['images'] += downloaded['images']
                                total_media['videos'] += downloaded['videos']
                                
                                if downloaded['images'] > 0 or downloaded['videos'] > 0:
                                    print(f"   + Downloaded: {downloaded['images']} images, {downloaded['videos']} videos")
                            
                            posts.append(post)

                            # Fetch comments for NEWLY-DISCOVERED posts using the
                            # age-aware policy (full tree while new). Already-stored
                            # threads get their new comments via the separate smart
                            # refresh pass, so we don't re-fetch them here.
                            is_new_post = post['permalink'] not in SEEN_URLS
                            if (scrape_comments_flag and post['num_comments'] > 0
                                    and is_new_post):
                                sort, depth, policy_cmin = comment_fetch_policy(post['created_utc'])
                                cmin = max(policy_cmin, comment_score_min)  # explicit arg can only tighten
                                kind = "full" if sort is None else f"top>={cmin}"
                                print(f"   💬 Fetching comments ({kind}) for: {post['title'][:40]}...")
                                comments = scrape_comments(post['permalink'], max_depth=depth,
                                                           sort=sort, score_min=cmin)
                                batch_comments.extend(comments)
                                total_comments += len(comments)
                                # Enrich (sentiment + tickers) then persist to the DB.
                                if not dry_run and comments:
                                    try:
                                        from analytics.enrich import enrich_comments
                                        from export.database import save_comments_batch, save_ticker_mentions
                                        c_mentions = enrich_comments(comments, target)
                                        save_comments_batch(comments, post['id'], upsert=refresh)
                                        save_ticker_mentions(c_mentions)
                                    except Exception as e:
                                        print(f"   ⚠️ Comment enrich/save failed: {e}")
                                time.sleep(1)
                        
                        # Collect for plugins
                        all_scraped_posts.extend(posts)
                        all_scraped_comments.extend(batch_comments)
                        
                        # Save data to the SQLite DB only (single source of truth)
                        if not dry_run:
                            try:
                                from analytics.enrich import enrich_posts
                                from export.database import save_posts_batch, save_ticker_mentions
                                p_mentions = enrich_posts(posts, target)
                                saved = save_posts_batch(posts, target, upsert=refresh)
                                save_ticker_mentions(p_mentions)
                            except Exception as e:
                                print(f"   ⚠️ DB save (posts) failed: {e}")
                                saved = 0
                            total_posts += saved
                            # Track these permalinks so later batches dedupe correctly.
                            for p in posts:
                                SEEN_URLS.add(p['permalink'])
                            print(f"✅ Saved {saved} new posts to DB")
                            # Always fetch account age for newly-discovered authors (cached).
                            try:
                                ensure_author_ages({p.get('author') for p in posts}
                                                   | {c.get('author') for c in batch_comments})
                            except Exception:
                                pass
                        else:
                            # In dry run, just count
                            total_posts += len(posts)
                            print(f"   🧪 [DRY RUN] Would save {len(posts)} posts")
                        
                        print(f"\n📊 Progress: {total_posts}/{limit} posts")
                        print(f"   🖼️  Images: {total_media['images']} | 🎬 Videos: {total_media['videos']}")
                        print(f"   💬 Comments: {total_comments}")
                        _scheduler_heartbeat()  # keep heartbeat fresh during long /new pass
                        
                        after = data['data'].get('after')
                        if not after:
                            print("\n🏁 Reached end of available history.")
                            break
                        
                        success = True
                        break
                        
                except Exception as e:
                    print(f"   ⚠️ Error with {base_url}: {e}")
                    continue
            
            if not after:
                break
                
            if not success:
                print("\n❌ All sources failed. Waiting 30s...")
                time.sleep(30)
            else:
                print(f"\n⏸️ Cooling down (3s)...")
                time.sleep(3)
        
        # Run plugins on collected data
        if use_plugins and (all_scraped_posts or all_scraped_comments):
            print("\n🔌 Running post-processing plugins...")
            try:
                from plugins import load_plugins, run_plugins
                plugins = load_plugins()
                if plugins:
                    all_scraped_posts, all_scraped_comments = run_plugins(
                        all_scraped_posts, all_scraped_comments, plugins
                    )
                    print(f"   ✅ Processed {len(all_scraped_posts)} posts with {len(plugins)} plugins")
                else:
                    print("   ⚠️ No plugins found")
            except Exception as e:
                print(f"   ⚠️ Plugin error: {e}")
    
    except Exception as e:
        error_msg = str(e)
        print(f"\n❌ Scrape error: {e}")
    
    duration = time.time() - start_time

    # Update subreddit tracking (last_scraped + totals) for subreddit scrapes.
    if not dry_run and not is_user and total_posts > 0:
        try:
            from export.database import update_subreddit_tracking
            update_subreddit_tracking(
                target, total_posts, total_comments,
                total_media['images'] + total_media['videos']
            )
        except Exception as e:
            print(f"⚠️ Subreddit tracking update failed: {e}")

    # Complete job tracking
    if job_id:
        try:
            status = 'failed' if error_msg else 'completed'
            complete_job_record(
                job_id, status, 
                total_posts, total_comments, 
                total_media['images'] + total_media['videos'],
                error_msg
            )
        except Exception as e:
            print(f"⚠️ Failed to complete job record: {e}")
    
    # Summary
    print("\n" + "=" * 50)
    if dry_run:
        print("🧪 DRY RUN COMPLETE!")
        print(f"   📊 Would scrape: {total_posts} posts")
        print(f"   💬 Would scrape: {total_comments} comments")
    else:
        print("✅ SCRAPE COMPLETE!")
        print(f"   📁 Data saved to: {dirs['base']}")
        print(f"   📊 Total posts: {total_posts}")
        print(f"   🖼️  Total images: {total_media['images']}")
        print(f"   🎬 Total videos: {total_media['videos']}")
        print(f"   💬 Total comments: {total_comments}")
    print(f"   ⏱️  Duration: {duration:.1f}s")
    
    return {
        'posts': total_posts,
        'images': total_media['images'],
        'videos': total_media['videos'],
        'comments': total_comments,
        'duration': f"{duration:.1f}s",
        'dry_run': dry_run,
        'job_id': job_id
    }

# --- MONITOR MODE ---
def run_monitor(target, is_user=False):
    prefix = "u" if is_user else "r"
    if is_user:
        rss_url = f"https://www.reddit.com/user/{target}/submitted.rss?limit=100"
    else:
        rss_url = f"https://www.reddit.com/r/{target}/new.rss?limit=100"

    print(f"[{datetime.datetime.now()}] 📡 Checking RSS for {prefix}/{target}...")
    
    try:
        rotate_session_proxy(force_rotate=True)
        response = SESSION.get(rss_url, timeout=15)
        
        if response.status_code != 200:
            print(f"❌ RSS blocked (Status {response.status_code}), trying JSON...")
            run_full_history(target, 25, is_user, download_media_flag=False, scrape_comments_flag=False)
            return

        root = ET.fromstring(response.content)
        namespace = {'atom': 'http://www.w3.org/2005/Atom'}
        posts = []
        
        for entry in root.findall('atom:entry', namespace):
            posts.append({
                "id": "",
                "title": entry.find('atom:title', namespace).text,
                "author": "",
                "created_utc": entry.find('atom:published', namespace).text,
                "permalink": entry.find('atom:link', namespace).attrib['href'],
                "url": entry.find('atom:link', namespace).attrib['href'],
                "score": 0,
                "upvote_ratio": 0,
                "num_comments": 0,
                "num_crossposts": 0,
                "selftext": "",
                "post_type": "unknown",
                "is_nsfw": False,
                "is_spoiler": False,
                "flair": "",
                "total_awards": 0,
                "has_media": False,
                "media_downloaded": False,
                "source": "Monitor-RSS"
            })
        
        dirs = setup_directories(target, prefix)
        save_posts_csv(posts, dirs["posts"])

    except Exception as e:
        print(f"❌ Monitor Error: {e}")

# --- CLI ---
def main():
    parser = argparse.ArgumentParser(
        description="🤖 Universal Reddit Scraper Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  SCRAPING:
    python main.py <target> --mode full --limit 100
    python main.py <target> --mode history --limit 500
    python main.py <target> --mode monitor
    python main.py <target> --dry-run           # Test without saving
    python main.py <target> --plugins           # Enable post-processing
    
  SEARCH:
    python main.py --search "keyword" --subreddit delhi
    python main.py --search "keyword" --min-score 100
    
  DASHBOARD:
    python main.py --dashboard
    
  SCHEDULE:
    python main.py --schedule delhi --every 60
    
  ANALYTICS:
    python main.py --analyze delhi --sentiment
    python main.py --analyze delhi --keywords
    
  MAINTENANCE:
    python main.py --job-history                # View job history
    python main.py --backup                     # Backup database
    python main.py --vacuum                     # Optimize database
    python main.py --export-parquet python      # Export to Parquet
    python main.py --list-plugins               # List available plugins
    
  REST API:
    python main.py --api                        # Start REST API server
        """
    )
    
    # Scraping args
    parser.add_argument("target", nargs='?', help="Subreddit or username to scrape")
    parser.add_argument("--mode", choices=["monitor", "history", "full"], default="full")
    parser.add_argument("--user", action="store_true", help="Target is a user")
    parser.add_argument("--limit", type=int, default=100, help="Max posts to scrape")
    parser.add_argument("--max-age-days", type=int, default=None,
                        help="Only scrape posts newer than N days (e.g. 30 for the last month)")
    parser.add_argument("--comment-score-min", type=int, default=0,
                        help="Only fetch comments for posts with score >= N (0 = all posts)")
    parser.add_argument("--refresh", action="store_true",
                        help="Re-fetch posts already stored and update their scores/comments (upsert)")
    parser.add_argument("--update-all", action="store_true",
                        help="Incrementally update every tracked subreddit in the DB")
    parser.add_argument("--enrich-backfill", action="store_true",
                        help="Backfill sentiment + ticker mentions + FTS over existing rows")
    parser.add_argument("--embed-backfill", action="store_true",
                        help="Embed any posts lacking a vector (semantic search)")
    parser.add_argument("--author-age-backfill", action="store_true",
                        help="Fetch + store account age for authors (coordination signal)")
    parser.add_argument("--price-backfill", action="store_true",
                        help="Fetch EOD prices for tracked tickers (hit-rate validation)")
    parser.add_argument("--author-status-revalidate", action="store_true",
                        help="Re-check author lifecycle status (deleted/suspended = pump signal)")
    parser.add_argument("--build-features", action="store_true",
                        help="Rebuild the point-in-time feature store (feature_daily + aggregate_daily)")
    parser.add_argument("--backtest", action="store_true",
                        help="Run the friction-aware event-study backtest over the feature store")
    parser.add_argument("--archive-backfill", action="store_true",
                        help="Deep historical backfill via arctic-shift (past Reddit's 1000-post cap)")
    parser.add_argument("--archive-comment-backfill", action="store_true",
                        help="Historical comments for stored ticker/megathread posts via arctic-shift")
    parser.add_argument("--aggregate-history-backfill", action="store_true",
                        help="Extend RRAI history (aggregate-only, no raw posts) via arctic-shift")
    parser.add_argument("--history-start", type=str, default="2020-01-01",
                        help="Start date for --aggregate-history-backfill (YYYY-MM-DD)")
    parser.add_argument("--history-subs", type=str, default="wallstreetbets,stocks,pennystocks",
                        help="Comma-separated subs for --aggregate-history-backfill")
    parser.add_argument("--history-post-level", action="store_true",
                        help="Aggregate ALL posts' sentiment (non-US subs; no US ticker gating)")
    parser.add_argument("--history-table", type=str, default="aggregate_daily",
                        help="Target table for --aggregate-history-backfill (e.g. au_aggregate_daily)")
    parser.add_argument("--export-signals", action="store_true",
                        help="Export capitulation dates to data/rrai_capitulation.csv (MT5 tester)")
    parser.add_argument("--no-media", action="store_true", help="Skip media download")
    parser.add_argument("--no-comments", action="store_true", help="Skip comments")
    
    # Dashboard
    parser.add_argument("--dashboard", action="store_true", help="Launch web dashboard")
    
    # Search
    parser.add_argument("--search", type=str, help="Search scraped data")
    parser.add_argument("--subreddit", type=str, help="Filter by subreddit")
    parser.add_argument("--min-score", type=int, help="Filter by minimum score")
    parser.add_argument("--author", type=str, help="Filter by author")
    
    # Analytics
    parser.add_argument("--analyze", type=str, help="Run analytics on subreddit")
    parser.add_argument("--sentiment", action="store_true", help="Run sentiment analysis")
    parser.add_argument("--keywords", action="store_true", help="Extract keywords")
    
    # Schedule
    parser.add_argument("--schedule", type=str, help="Schedule scraping for target")
    parser.add_argument("--every", type=int, help="Interval in minutes")
    
    # Alerts
    parser.add_argument("--alert", type=str, help="Set keyword alert")
    parser.add_argument("--discord-webhook", type=str, help="Discord webhook URL")
    parser.add_argument("--telegram-token", type=str, help="Telegram bot token")
    parser.add_argument("--telegram-chat", type=str, help="Telegram chat ID")
    
    # New: Observability & Maintenance
    parser.add_argument("--dry-run", action="store_true", help="Simulate scrape without saving data")
    parser.add_argument("--plugins", action="store_true", help="Enable post-processing plugins")
    parser.add_argument("--list-plugins", action="store_true", help="List available plugins")
    parser.add_argument("--job-history", action="store_true", help="View job history")
    parser.add_argument("--backup", action="store_true", help="Backup SQLite database")
    parser.add_argument("--vacuum", action="store_true", help="Optimize SQLite database")
    parser.add_argument("--export-parquet", type=str, help="Export subreddit to Parquet format")
    parser.add_argument("--api", action="store_true", help="Start REST API server (port 8000)")
    parser.add_argument("--mcp", action="store_true", help="Start MCP server (streamable-HTTP, port 8765)")
    parser.add_argument("--proxy", type=str, help="Proxy URL (e.g. http://username:password@host:port)")
    parser.add_argument("--proxy-country", type=str, help="Target country code for ScrapingAnt proxies (e.g. US, IN)")
    parser.add_argument("--proxy-session", type=str, help="Persistent session ID for ScrapingAnt proxies")
    parser.add_argument("--no-proxy-rotate", action="store_true", help="Disable automatic session rotation")
    
    args = parser.parse_args()

    # Reddit Official API (OAuth): use oauth.reddit.com and go direct (the public
    # mirrors / scraping proxies are unnecessary and Reddit blocks them).
    global MIRRORS
    if USE_REDDIT_OAUTH:
        MIRRORS = ["https://oauth.reddit.com"]
        SESSION.headers["User-Agent"] = REDDIT_USER_AGENT
        grant = "password (script app)" if (REDDIT_USERNAME and REDDIT_PASSWORD) else "client_credentials (app-only)"
        print(f"🔑 Using Reddit Official API (OAuth) - grant: {grant}")
        try:
            ensure_oauth_header()
            print("   ✅ OAuth token acquired")
        except Exception as e:
            print(f"   ❌ OAuth token failed: {e}")

    # Configure Proxy (skipped in OAuth mode - direct connection to oauth.reddit.com)
    global PROXY_TO_USE
    PROXY_TO_USE = "" if USE_REDDIT_OAUTH else (args.proxy if args.proxy is not None else PROXY_URL)
    
    # Apply CLI overrides to configuration if provided
    if args.proxy_country:
        import config
        config.PROXY_COUNTRY = args.proxy_country
    if args.proxy_session:
        import config
        config.PROXY_SESSION_ID = args.proxy_session
    if args.no_proxy_rotate:
        import config
        config.PROXY_AUTO_ROTATE = False
        
    if PROXY_TO_USE and PROXY_TO_USE.lower() not in ["none", "direct", "disabled", ""]:
        # Do initial proxy rotation/setup
        rotate_session_proxy(force_rotate=False)

        # ScrapingAnt's HTTPS proxy tunnel requires disabling SSL verification
        # (the InsecureRequestWarning is already suppressed at module load).
        if "scrapingant" in PROXY_TO_USE.lower():
            SESSION.verify = False

        try:
            current_proxy = SESSION.proxies.get("https", PROXY_TO_USE)
            parsed = urlparse(current_proxy)
            if parsed.username:
                masked_proxy = f"{parsed.scheme}://{parsed.username}:*****@{parsed.hostname}"
                if parsed.port:
                    masked_proxy += f":{parsed.port}"
            else:
                masked_proxy = current_proxy
        except Exception:
            masked_proxy = "[Invalid Proxy URL]"
        print(f"🔒 Using Proxy: {masked_proxy}")
    else:
        if "HTTP_PROXY" in os.environ:
            del os.environ["HTTP_PROXY"]
        if "HTTPS_PROXY" in os.environ:
            del os.environ["HTTPS_PROXY"]
        SESSION.proxies = {}
        if PROXY_TO_USE and PROXY_TO_USE.lower() in ["none", "direct", "disabled"]:
            print("🚫 Proxy explicitly disabled (Direct connection)")
        
    print("=" * 50)
    print("🤖 UNIVERSAL REDDIT SCRAPER SUITE")
    print("=" * 50)
    
    # Dashboard mode
    if args.dashboard:
        print("\n🌐 Launching Dashboard...")
        print("   Open: http://localhost:8501")
        os.system("streamlit run dashboard/app.py")
        return
    
    # REST API mode
    if args.api:
        print("\n🚀 Starting REST API server...")
        print("   📖 Docs: http://localhost:8000/docs")
        print("   📊 Connect Metabase/Grafana to http://localhost:8000")
        try:
            import uvicorn
            from api.server import app
            uvicorn.run(app, host="0.0.0.0", port=8000)
        except ImportError:
            print("❌ Install dependencies: pip install fastapi uvicorn")
        return

    # MCP server mode
    if args.mcp:
        print("\n🧠 Starting MCP server (streamable-HTTP)...")
        print("   🔌 Endpoint: http://localhost:8765/mcp  (Bearer-protected)")
        print("   ❤️  Health:   http://localhost:8765/healthz")
        try:
            from mcp_server.server import run
            run()
        except ImportError as e:
            print(f"❌ Install dependencies: pip install mcp httpx  ({e})")
        return
    
    # --- NEW: Maintenance & Observability Commands ---
    
    # Job history
    if args.job_history:
        from export.database import print_job_history
        print_job_history()
        return
    
    # Backup database
    if args.backup:
        from export.database import backup_database
        backup_database()
        return
    
    # Vacuum/optimize database
    if args.vacuum:
        from export.database import vacuum_database
        vacuum_database()
        return
    
    # Export to Parquet
    if args.export_parquet:
        from export.parquet import export_to_parquet
        prefix = "u" if args.user else "r"
        export_to_parquet(args.export_parquet, prefix=prefix)
        return
    
    # List plugins
    if args.list_plugins:
        from plugins import list_plugins
        list_plugins()
        return
    
    # Search mode
    if args.search:
        print(f"\n🔍 Searching for: {args.search}")
        from search.query import search_all_data, print_search_results
        
        results = search_all_data(
            query=args.search,
            min_score=args.min_score,
            author=args.author
        )
        print_search_results(results)
        return
    
    # Analytics mode
    if args.analyze:
        print(f"\n📊 Analyzing: {args.analyze}")
        
        # Load data
        data_dir = Path(f"data/r_{args.analyze}")
        if not data_dir.exists():
            print(f"❌ No data found for r/{args.analyze}")
            return
        
        posts_file = data_dir / "posts.csv"
        if not posts_file.exists():
            print(f"❌ No posts data found")
            return
        
        import pandas as pd
        df = pd.read_csv(posts_file)
        posts = df.to_dict('records')
        
        if args.sentiment:
            from analytics.sentiment import analyze_posts_sentiment
            analyzed, counts = analyze_posts_sentiment(posts)
            print(f"\n😀 Sentiment Analysis:")
            print(f"   Positive: {counts['positive']}")
            print(f"   Neutral:  {counts['neutral']}")
            print(f"   Negative: {counts['negative']}")
        
        if args.keywords:
            from analytics.sentiment import extract_keywords
            texts = [str(p.get('title', '') or '') + ' ' + str(p.get('selftext', '') or '') for p in posts]
            keywords = extract_keywords(texts, top_n=20)
            print(f"\n☁️ Top Keywords:")
            for word, count in keywords:
                print(f"   {word}: {count}")
        
        return
    
    # Schedule mode
    if args.schedule:
        if not args.every:
            print("❌ Please specify --every <minutes>")
            return
        
        from scheduler.cron import run_scheduled
        run_scheduled(args.schedule, args.every, args.mode, args.limit, args.user)
        return

    # Enrich-backfill: sentiment + ticker mentions + FTS over existing rows.
    if args.enrich_backfill:
        print("🧠 Enriching existing rows (sentiment + tickers + FTS)...")
        from analytics.enrich import enrich_backfill
        enrich_backfill()
        return

    # Embed-backfill: vectorize posts for semantic search.
    if args.embed_backfill:
        print("🧬 Embedding posts (semantic search)...")
        from analytics.embeddings import embed_backfill
        embed_backfill()
        return

    # Author-age backfill: fetch account creation dates (coordination signal).
    if args.author_age_backfill:
        print("👤 Fetching author account ages...")
        author_age_backfill()
        return

    # Price backfill: fetch EOD closes for tracked tickers (hit-rate validation).
    if args.price_backfill:
        print("💲 Fetching EOD prices for tracked tickers...")
        from analytics.prices import price_backfill, tracked_tickers
        print(price_backfill(tracked_tickers(min_mentions=3)))
        return

    # Author status revalidation: catch accounts deleted/suspended after we scraped them.
    if args.author_status_revalidate:
        print("🔁 Revalidating author lifecycle status...")
        print(author_status_revalidate(max_authors=5000, stale_days=0))
        return

    # Feature store rebuild: point-in-time features for the signal framework.
    if args.build_features:
        print("🧮 Rebuilding point-in-time feature store...")
        from analytics.features import build_all
        print(build_all())
        return

    # Backtest: friction-aware event study over the feature store.
    if args.backtest:
        from backtest.engine import main as bt_main
        bt_main()
        return

    # Archive backfill: deep historical posts via arctic-shift (past the /new cap).
    if args.archive_backfill:
        from scraper.archive import archive_backfill
        from export.database import get_all_subreddits
        days = args.max_age_days or 180
        targets = [args.target] if args.target else [s["subreddit"] for s in get_all_subreddits()]
        print(f"📜 Archive backfill ({days}d) for {len(targets)} subreddit(s)...")
        grand = 0
        for sub in targets:
            try:
                grand += archive_backfill(sub, extract_post_data, days=days)
            except Exception as e:
                print(f"   ⚠️ archive backfill failed for r/{sub}: {e}")
        print(f"📜 Archive backfill complete: {grand} historical posts added")
        return

    # Archive COMMENT backfill: historical comments for stored ticker/megathread posts.
    if args.archive_comment_backfill:
        from scraper.archive import archive_comment_backfill
        from export.database import get_all_subreddits
        days = args.max_age_days or 180
        targets = [args.target] if args.target else [s["subreddit"] for s in get_all_subreddits()]
        print(f"💬 Archive comment backfill ({days}d) for {len(targets)} subreddit(s)...")
        grand = 0
        for sub in targets:
            try:
                grand += archive_comment_backfill(sub, days=days)
            except Exception as e:
                print(f"   ⚠️ comment backfill failed for r/{sub}: {e}")
        print(f"💬 Archive comment backfill complete: {grand} historical comments added")
        return

    # Aggregate-history backfill: extend the RRAI series back in time (aggregate-only,
    # no raw posts) so the macro signal can be tested across multiple market regimes.
    if args.aggregate_history_backfill:
        from scraper.archive import archive_aggregate_backfill
        from analytics.features import recompute_rrai_pct
        subs = [s.strip() for s in args.history_subs.split(",") if s.strip()]
        # Iterate to today; already-present days (recent block + any done) are skipped, so
        # this fills exactly the historical gaps regardless of what's already stored.
        end = datetime.datetime.now().strftime("%Y-%m-%d")
        print(f"📈 Aggregate-history backfill {args.history_start}..{end}  subs={subs} "
              f"table={args.history_table} post_level={args.history_post_level}")
        archive_aggregate_backfill(subs, args.history_start, end,
                                   post_level=args.history_post_level, table=args.history_table)
        print(recompute_rrai_pct(table=args.history_table))
        return

    # Export capitulation dates for the MT5 Strategy Tester (Common\Files\rrai_capitulation.csv).
    if args.export_signals:
        from export.database import get_connection as _gc
        dates = [r["date"][:10].replace("-", ".") for r in _gc().execute(
            "SELECT date FROM aggregate_daily WHERE rrai_pct<=0.15 ORDER BY date").fetchall()]
        with open("data/rrai_capitulation.csv", "w") as f:
            f.write("\n".join(dates) + "\n")
        print(f"Exported {len(dates)} capitulation dates -> data/rrai_capitulation.csv"
              + (f" ({dates[0]}..{dates[-1]})" if dates else ""))
        return

    # Update-all mode: incrementally refresh every tracked subreddit in the DB.
    # With --every N it loops continuously (used by the scheduler service).
    if args.update_all:
        from export.database import get_all_subreddits

        def update_cycle():
            subs = get_all_subreddits()
            if not subs:
                print("ℹ️  No tracked subreddits yet — scrape one first.")
                return
            print(f"🔄 Updating {len(subs)} subreddit(s): {', '.join(s['subreddit'] for s in subs)}")
            for s in subs:
                name = s['subreddit']
                print(f"\n=== Updating r/{name} ===")
                try:
                    run_full_history(
                        name, args.limit, is_user=False,
                        download_media_flag=False,
                        scrape_comments_flag=not args.no_comments,
                        max_age_days=args.max_age_days,
                        comment_score_min=args.comment_score_min,
                        refresh=args.refresh,
                    )
                    # Capture new comments on EXISTING threads (smart change-detection,
                    # age-aware retrieval) — the /new pass above only handles new posts.
                    if not args.no_comments:
                        refresh_existing_comments(name, since_days=COMMENT_REFRESH_DAYS)
                except Exception as e:
                    print(f"⚠️ Update failed for r/{name}: {e}")
                _scheduler_heartbeat()  # heartbeat between subreddits
            # Keep FTS fresh for edits/deletes (new inserts are indexed live by triggers).
            try:
                from export.database import rebuild_fts
                rebuild_fts()
            except Exception:
                pass
            # Embed any newly-scraped posts (incremental; cheap after the first cycle).
            try:
                from analytics.embeddings import embed_backfill
                embed_backfill()
            except Exception as e:
                print(f"⚠️ Embed step skipped: {e}")
            # Refresh the daily ticker rollups.
            try:
                from export.database import update_daily_stats
                update_daily_stats()
            except Exception:
                pass
            # Incrementally fetch account ages for newly-seen authors (rate-limited).
            try:
                author_age_backfill(max_authors=150)
            except Exception as e:
                print(f"⚠️ Author age step skipped: {e}")
            # Re-check lifecycle status of recently-active authors (deleted/suspended =
            # the pump-confirmation signal). Small batch per cycle so it stays cheap.
            try:
                author_status_revalidate(max_authors=150, stale_days=7)
            except Exception as e:
                print(f"⚠️ Author status step skipped: {e}")
            # Fetch EOD prices for any newly-tracked tickers (hit-rate validation).
            try:
                from analytics.prices import price_backfill, tracked_tickers
                price_backfill(tracked_tickers(min_mentions=3)[:60])
            except Exception as e:
                print(f"⚠️ Price step skipped: {e}")
            # Keep macro/overlay instruments fresh (index + risk-FX for the RRAI overlay).
            try:
                from analytics.prices import fetch_prices
                for _m in ("SPY", "QQQ", "AUDJPY=X", "GC=F", "SI=F"):  # index/FX + gold/silver
                    fetch_prices(_m)
            except Exception as e:
                print(f"⚠️ Macro price step skipped: {e}")
            # Rebuild the point-in-time feature store (cheap; keeps signals current).
            try:
                from analytics.features import build_all
                build_all()
            except Exception as e:
                print(f"⚠️ Feature-store step skipped: {e}")

        if args.every:
            from scheduler import control as sched_control

            print(f"⏰ Continuous update every {args.every} min "
                  f"(control file: {sched_control.CONTROL_PATH})")
            # Initial heartbeat so health checks / status tools see liveness immediately.
            sched_control.write_status(heartbeat=sched_control.now_iso(),
                                       heartbeat_epoch=time.time(),
                                       interval_minutes=args.every, enabled=True)
            while True:
                ctrl = sched_control.read_control()
                enabled = ctrl.get("enabled", True)
                interval = ctrl.get("interval_minutes") or args.every

                if enabled:
                    started = time.time()
                    sched_control.write_status(last_run_started=sched_control.now_iso(),
                                               heartbeat=sched_control.now_iso(),
                                               heartbeat_epoch=started)
                    ok = True
                    try:
                        update_cycle()
                    except Exception as e:  # update_cycle guards per-sub, but be safe
                        ok = False
                        print(f"⚠️ Update cycle failed: {e}")
                    sched_control.write_status(last_run_finished=sched_control.now_iso(),
                                               last_run_ok=ok)
                else:
                    print("⏸️  Scheduler paused via control file — skipping this cycle.")

                # Re-read interval after the cycle, then pick a RANDOM gap before the
                # next cycle (uniform 15s..min(interval, 5min)) — avoids a robotic
                # fixed cadence.
                interval = sched_control.read_control().get("interval_minutes") or args.every
                sleep_seconds = sched_control.pick_sleep_seconds(interval)
                next_epoch = time.time() + sleep_seconds
                sched_control.write_status(
                    enabled=enabled, interval_minutes=interval,
                    next_sleep_seconds=round(sleep_seconds, 1),
                    next_run_iso=datetime.datetime.fromtimestamp(
                        next_epoch, datetime.timezone.utc).isoformat(),
                    next_run_epoch=next_epoch)
                cap = sched_control.effective_max_gap_seconds(interval)
                print(f"\n😴 Sleeping {sleep_seconds:.0f}s "
                      f"(randomised {sched_control.MIN_GAP_SECONDS}s..{cap:.0f}s) "
                      f"until next update cycle...")

                # Interruptible sleep: wake every 5s to refresh the heartbeat and to
                # pick up a changed interval / pause without waiting out the gap.
                while time.time() < next_epoch:
                    time.sleep(min(5, max(0.5, next_epoch - time.time())))
                    sched_control.write_status(heartbeat=sched_control.now_iso(),
                                               heartbeat_epoch=time.time())
                    new_interval = sched_control.read_control().get("interval_minutes") or args.every
                    if new_interval != interval:
                        # Cadence changed mid-sleep — re-pick a random gap from now.
                        interval = new_interval
                        sleep_seconds = sched_control.pick_sleep_seconds(interval)
                        next_epoch = time.time() + sleep_seconds
        else:
            update_cycle()
        return

    # Regular scraping mode
    if not args.target:
        parser.print_help()
        return
    
    if args.mode == "monitor":
        prefix = "u" if args.user else "r"
        dirs = setup_directories(args.target, prefix)
        load_history(dirs["posts"])
        print(f"🔄 Monitoring {prefix}/{args.target} every 5 mins...")
        while True:
            run_monitor(args.target, args.user)
            time.sleep(300)
    elif args.mode == "history":
        run_full_history(args.target, args.limit, args.user,
                        download_media_flag=False, scrape_comments_flag=False,
                        dry_run=args.dry_run, use_plugins=args.plugins,
                        max_age_days=args.max_age_days,
                        comment_score_min=args.comment_score_min, refresh=args.refresh)
    else:
        run_full_history(args.target, args.limit, args.user,
                        download_media_flag=not args.no_media,
                        scrape_comments_flag=not args.no_comments,
                        dry_run=args.dry_run, use_plugins=args.plugins,
                        max_age_days=args.max_age_days,
                        comment_score_min=args.comment_score_min, refresh=args.refresh)

if __name__ == "__main__":
    main()
