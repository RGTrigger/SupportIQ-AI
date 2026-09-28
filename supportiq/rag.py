from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import Any

from supportiq.db import connect, now_iso


def _embed(texts: list[str]) -> list[list[float]]:
    """Use SentenceTransformers when its model is cached; deterministic fallback is offline-safe."""
    try:
        from sentence_transformers import SentenceTransformer
        global _MODEL
        if "_MODEL" not in globals():
            model=os.getenv("SUPPORTIQ_EMBEDDING_MODEL")
            if not model:
                try:
                    import streamlit as st
                    model=st.secrets.get("SUPPORTIQ_EMBEDDING_MODEL")
                except Exception:
                    model=None
            _MODEL = SentenceTransformer(model or "all-MiniLM-L6-v2", progress_bar=False)
        return _MODEL.encode(texts, normalize_embeddings=True).tolist()
    except Exception:
        # Stable signed feature hashing provides a small, deterministic offline vector space.
        vectors = []
        for text in texts:
            v = [0.0] * 256
            for token in re.findall(r"[\w₹]+", text.lower()):
                raw = hashlib.blake2b(token.encode(), digest_size=8).digest()
                idx = int.from_bytes(raw[:4], "little") % len(v)
                v[idx] += 1.0 if raw[4] & 1 else -1.0
            norm = sum(x*x for x in v) ** .5 or 1
            vectors.append([x/norm for x in v])
        return vectors


def _collection(path: Path):
    import chromadb
    client = chromadb.PersistentClient(path=str(path))
    return client.get_or_create_collection("supportiq_knowledge", metadata={"hnsw:space":"cosine"})


def _chunks(text: str, size: int = 850, overlap: int = 120) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    return [text[start:start + size] for start in range(0, len(text), size - overlap)]


def index_text(path: Path, vector_path: Path, filename: str, text: str, document_type: str = "Support document") -> int:
    parts = _chunks(text)
    if not parts:
        raise ValueError("The selected document does not contain readable text.")
    doc_id = str(uuid.uuid4())
    stamp = now_iso()
    collection = _collection(vector_path)
    collection.add(ids=[f"{doc_id}:{i}" for i in range(len(parts))], documents=parts, embeddings=_embed(parts), metadatas=[{"document_id":doc_id,"filename":filename,"document_type":document_type,"chunk_index":i,"source":"Uploaded knowledge"} for i in range(len(parts))])
    with connect(path) as db:
        db.execute("INSERT INTO knowledge_documents VALUES(?,?,?,?,?,?,?)", (doc_id,filename,document_type,"Indexed",len(parts),stamp,stamp))
        for i, part in enumerate(parts):
            db.execute("INSERT INTO knowledge_chunks VALUES(?,?,?,?,?)", (f"{doc_id}:{i}",doc_id,i,part,'{"source":"Uploaded knowledge"}'))
    return len(parts)


def retrieve(path: Path, vector_path: Path, query: str, limit: int = 4) -> list[dict[str, Any]]:
    if not query.strip():
        return []
    try:
        collection = _collection(vector_path)
        if collection.count():
            result = collection.query(query_embeddings=_embed([query]), n_results=min(limit, collection.count()), include=["documents","metadatas","distances"])
            hits=[]
            for doc, meta, dist in zip(result["documents"][0], result["metadatas"][0], result["distances"][0]):
                hits.append({"content":doc,"source":meta.get("filename","Knowledge document"),"document_type":meta.get("document_type"),"score":round(max(0.0,1-float(dist)),3),"chunk_index":meta.get("chunk_index")})
            return hits
    except Exception:
        pass
    # SQLite fallback preserves usefulness if the vector engine cannot start.
    tokens = set(re.findall(r"[\w₹]+", query.lower()))
    with connect(path) as db:
        docs = db.execute("SELECT c.content,d.filename,d.document_type,c.chunk_index FROM knowledge_chunks c JOIN knowledge_documents d ON d.id=c.document_id").fetchall()
    scored=[]
    for row in docs:
        words=set(re.findall(r"[\w₹]+",row[0].lower())); score=len(tokens & words)/max(1,len(tokens | words))
        if score: scored.append((score,row))
    return [{"content":r[0],"source":r[1],"document_type":r[2],"score":round(s,3),"chunk_index":r[3]} for s,r in sorted(scored,key=lambda x:x[0],reverse=True)[:limit]]


def sync_vector_index(path: Path, vector_path: Path) -> int:
    """Index SQLite knowledge chunks into Chroma, idempotently, including seeded demo guides."""
    with connect(path) as db:
        docs=[dict(r) for r in db.execute("SELECT c.id,c.content,d.id document_id,d.filename,d.document_type,c.chunk_index FROM knowledge_chunks c JOIN knowledge_documents d ON d.id=c.document_id")]
    if not docs: return 0
    try:
        collection=_collection(vector_path)
        known=set(collection.get(include=[])['ids'])
        missing=[r for r in docs if r['id'] not in known]
        if not missing: return 0
        texts=[r['content'] for r in missing]
        collection.add(ids=[r['id'] for r in missing],documents=texts,embeddings=_embed(texts),metadatas=[{"document_id":r["document_id"],"filename":r["filename"],"document_type":r["document_type"],"chunk_index":r["chunk_index"],"source":"Support knowledge"} for r in missing])
        return len(missing)
    except Exception:
        return 0


def reindex_document(path: Path, vector_path: Path, document_id: str) -> int:
    with connect(path) as db:
        doc=db.execute("SELECT filename,document_type FROM knowledge_documents WHERE id=?",(document_id,)).fetchone()
        chunks=[dict(r) for r in db.execute("SELECT id,content,chunk_index FROM knowledge_chunks WHERE document_id=? ORDER BY chunk_index",(document_id,))]
    if not doc or not chunks: raise ValueError("Document or extracted text could not be found.")
    collection=_collection(vector_path)
    ids=[c["id"] for c in chunks]
    try: collection.delete(ids=ids)
    except Exception: pass
    texts=[c["content"] for c in chunks]
    collection.add(ids=ids,documents=texts,embeddings=_embed(texts),metadatas=[{"document_id":document_id,"filename":doc["filename"],"document_type":doc["document_type"],"chunk_index":c["chunk_index"],"source":"Uploaded knowledge"} for c in chunks])
    with connect(path) as db: db.execute("UPDATE knowledge_documents SET status='Indexed',chunk_count=?,indexed_at=? WHERE id=?",(len(chunks),now_iso(),document_id))
    return len(chunks)


def delete_document(path: Path, vector_path: Path, document_id: str) -> None:
    with connect(path) as db:
        chunks=[r[0] for r in db.execute("SELECT id FROM knowledge_chunks WHERE document_id=?",(document_id,))]
        if not db.execute("SELECT 1 FROM knowledge_documents WHERE id=?",(document_id,)).fetchone(): raise ValueError("Document not found.")
    if chunks:
        collection=_collection(vector_path)
        collection.delete(ids=chunks)
    with connect(path) as db: db.execute("DELETE FROM knowledge_documents WHERE id=?",(document_id,))


def extract_text(filename: str, content: bytes) -> str:
    if len(content) > 10 * 1024 * 1024:
        raise ValueError("Knowledge files must be 10 MB or smaller.")
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md", ".csv"}:
        text = content.decode("utf-8", errors="replace")
        if len(text) > 2_000_000: raise ValueError("Extracted knowledge text exceeds the 2 MB character limit.")
        return text
    if suffix == ".pdf":
        from pypdf import PdfReader
        import io
        reader=PdfReader(io.BytesIO(content))
        if len(reader.pages) > 200: raise ValueError("PDFs may contain at most 200 pages.")
        text="\n".join(page.extract_text() or "" for page in reader.pages)
        if len(text) > 2_000_000: raise ValueError("Extracted knowledge text exceeds the 2 MB character limit.")
        return text
    if suffix == ".docx":
        from docx import Document
        import io
        text="\n".join(p.text for p in Document(io.BytesIO(content)).paragraphs)
        if len(text) > 2_000_000: raise ValueError("Extracted knowledge text exceeds the 2 MB character limit.")
        return text
    raise ValueError("Supported formats: PDF, DOCX, TXT, Markdown, and CSV.")
