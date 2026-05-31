"""Embeddings + semantic search with a pluggable backend.

Default: fastembed bge-small-en-v1.5 (offline, 384-d). If VOYAGE_API_KEY is set,
switches to Voyage voyage-finance-2 (finance-tuned, 1024-d, API). Vectors are stored
as float32 BLOBs and searched with numpy brute-force cosine (sub-100ms at this scale).

Switching backend changes the vector dimension, so embed_backfill detects a model
change and re-embeds everything (the Voyage free tier easily absorbs it).
"""
import os

import numpy as np

from export.database import get_connection

_VOYAGE_KEY = os.getenv("VOYAGE_API_KEY", "").strip()
_USE_VOYAGE = bool(_VOYAGE_KEY)
_VOYAGE_MODEL = "voyage-finance-2"
# Offline model is configurable: bge-small (fast default) or e.g.
# mixedbread-ai/mxbai-embed-large-v1 (slower, higher retrieval quality).
_FE_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
_CACHE_DIR = "/app/data/models"

_MODEL = None


def active_model():
    return _VOYAGE_MODEL if _USE_VOYAGE else _FE_MODEL


def _active_dim():
    if _USE_VOYAGE:
        return 1024
    try:
        from fastembed import TextEmbedding
        for m in TextEmbedding.list_supported_models():
            if m["model"] == _FE_MODEL:
                return m["dim"]
    except Exception:
        pass
    return 384


def _fe():
    global _MODEL
    if _MODEL is None:
        from fastembed import TextEmbedding
        _MODEL = TextEmbedding(model_name=_FE_MODEL, cache_dir=_CACHE_DIR)
    return _MODEL


def _voyage_embed(texts, input_type):
    import requests
    out = []
    for i in range(0, len(texts), 128):  # Voyage caps batch size
        chunk = texts[i:i + 128]
        r = requests.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {_VOYAGE_KEY}"},
            json={"input": chunk, "model": _VOYAGE_MODEL, "input_type": input_type},
            timeout=60,
        )
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        out.extend(np.asarray(d["embedding"], dtype=np.float32) for d in data)
    return out


def _embed(texts, input_type="document"):
    texts = list(texts)
    if _USE_VOYAGE:
        return _voyage_embed(texts, input_type)
    return [np.asarray(v, dtype=np.float32) for v in _fe().embed(texts)]


def _to_blob(vec):
    return np.asarray(vec, dtype=np.float32).tobytes()


def _from_blob(b):
    return np.frombuffer(b, dtype=np.float32)


def ensure_table():
    conn = get_connection()
    conn.execute("CREATE TABLE IF NOT EXISTS post_embeddings (post_id TEXT PRIMARY KEY, embedding BLOB)")
    conn.commit()
    conn.close()


def embed_backfill(batch=128):
    """Embed posts lacking a vector. If the active backend's dimension differs from the
    stored vectors (i.e. the model was switched), clears and re-embeds everything so all
    vectors share one dimension."""
    ensure_table()
    conn = get_connection()
    cur = conn.cursor()
    sample = cur.execute("SELECT embedding FROM post_embeddings LIMIT 1").fetchone()
    if sample is not None and len(_from_blob(sample["embedding"])) != _active_dim():
        old_dim = len(_from_blob(sample["embedding"]))
        cur.execute("DELETE FROM post_embeddings")
        conn.commit()
        print(f"  embedding dim {old_dim} -> {_active_dim()} ({active_model()}): re-embedding all posts")
    total = 0
    while True:
        cur.execute("""SELECT id, title, selftext FROM posts
                       WHERE id NOT IN (SELECT post_id FROM post_embeddings) LIMIT ?""", (batch,))
        rows = cur.fetchall()
        if not rows:
            break
        texts = [f"{r['title'] or ''} {r['selftext'] or ''}".strip()[:2000] for r in rows]
        vecs = _embed(texts, input_type="document")
        for r, v in zip(rows, vecs):
            cur.execute("INSERT OR REPLACE INTO post_embeddings(post_id, embedding) VALUES(?, ?)",
                        (r["id"], _to_blob(v)))
        conn.commit()
        total += len(rows)
        print(f"  embedded {total} posts ({active_model()})")
    conn.close()
    return {"embedded": total, "model": active_model()}


def _load_matrix():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT post_id, embedding FROM post_embeddings")
    ids, mat = [], []
    for r in cur.fetchall():
        ids.append(r["post_id"])
        mat.append(_from_blob(r["embedding"]))
    conn.close()
    if not ids:
        return [], None
    return ids, np.vstack(mat)


def _topk(qvec, ids, M, k, exclude=None):
    q = np.asarray(qvec, dtype=np.float32)
    q = q / (np.linalg.norm(q) + 1e-9)
    Mn = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
    sims = Mn @ q
    out = []
    for i in np.argsort(-sims):
        if exclude and ids[i] == exclude:
            continue
        out.append((ids[i], float(sims[i])))
        if len(out) >= k:
            break
    return out


def _fetch_posts(pairs):
    conn = get_connection()
    cur = conn.cursor()
    res = []
    for pid, sim in pairs:
        row = cur.execute("""SELECT id, subreddit, title, score, num_comments, permalink,
                                    created_utc, sentiment_score FROM posts WHERE id=?""", (pid,)).fetchone()
        if row:
            d = dict(row)
            d["similarity"] = round(sim, 3)
            res.append(d)
    conn.close()
    return res


def semantic_search(query, k=10):
    ids, M = _load_matrix()
    if not ids:
        return {"query": query, "results": [], "model": active_model()}
    qv = _embed([query], input_type="query")[0]
    return {"query": query, "model": active_model(), "results": _fetch_posts(_topk(qv, ids, M, k))}


def similar_posts(post_id, k=10):
    ids, M = _load_matrix()
    if not ids:
        return {"post_id": post_id, "results": []}
    conn = get_connection()
    row = conn.execute("SELECT embedding FROM post_embeddings WHERE post_id=?", (post_id,)).fetchone()
    conn.close()
    if not row:
        return {"post_id": post_id, "results": [], "error": "post not embedded"}
    qv = _from_blob(row["embedding"])
    return {"post_id": post_id, "results": _fetch_posts(_topk(qv, ids, M, k, exclude=post_id))}


def summarize_ticker(ticker, window_days=7, k=15):
    """RAG retrieval: the most thesis-relevant recent posts mentioning the ticker."""
    from datetime import datetime, timedelta
    t = ticker.upper().lstrip("$")
    since = (datetime.now() - timedelta(days=window_days)).isoformat()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""SELECT DISTINCT source_id FROM ticker_mentions
                   WHERE ticker=? AND source_type='post' AND created_utc>=?""", (t, since))
    candidate = {r["source_id"] for r in cur.fetchall()}
    conn.close()
    if not candidate:
        return {"ticker": t, "window_days": window_days, "posts": []}
    ids, M = _load_matrix()
    if not ids:
        return {"ticker": t, "window_days": window_days, "posts": []}
    mask = [i for i, pid in enumerate(ids) if pid in candidate]
    if not mask:
        return {"ticker": t, "window_days": window_days, "posts": []}
    sub_ids = [ids[i] for i in mask]
    sub_M = M[mask]
    qv = _embed([f"{t} stock bull case bear case price target catalyst earnings"], input_type="query")[0]
    pairs = _topk(qv, sub_ids, sub_M, k)
    conn = get_connection()
    cur = conn.cursor()
    posts = []
    for pid, sim in pairs:
        row = cur.execute("""SELECT title, selftext, score, sentiment_score, permalink, subreddit, created_utc
                             FROM posts WHERE id=?""", (pid,)).fetchone()
        if row:
            d = dict(row)
            d["relevance"] = round(sim, 3)
            d["selftext"] = (d.get("selftext") or "")[:800]
            posts.append(d)
    conn.close()
    return {"ticker": t, "window_days": window_days, "model": active_model(),
            "post_count": len(posts), "posts": posts}
