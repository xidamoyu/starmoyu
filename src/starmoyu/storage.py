"""存储层：Milvus（向量）+ PostgreSQL（关系）+ MinIO（原件）。

职责划分：
  Milvus     —— 语料块的向量与元数据，负责语义检索与标量预过滤
  PostgreSQL —— 达人档案 / 商单台账 / 跟进流水等强关系型业务数据
  MinIO      —— 合同、结案报告、刊例 PDF 等原始文件（S3 兼容，支持预签名直链）
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# ---------------------------------------------------------------- 配置读取

def _load_env() -> None:
    """从项目根 .env 加载配置（不覆盖已有环境变量）。"""
    env = Path(__file__).resolve().parents[2] / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_env()

MILVUS_HOST = os.environ.get("MILVUS_HOST", "127.0.0.1")
MILVUS_PORT = os.environ.get("MILVUS_PORT", "19530")
MILVUS_DB = os.environ.get("MILVUS_DB", "starmoyu")

PG_HOST = os.environ.get("PG_HOST", "127.0.0.1")
PG_PORT = int(os.environ.get("PG_PORT", "5432"))
PG_USER = os.environ.get("PG_USER", "postgres")
PG_PASSWORD = os.environ.get("PG_PASSWORD", "")
PG_DB = os.environ.get("PG_DB", "starmoyu")

MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "127.0.0.1:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "")
MINIO_BUCKET = os.environ.get("MINIO_BUCKET", "starmoyu-raw")

COLLECTION = "dm_chunks"
EMBED_DIM = int(os.environ.get("EMBED_DIM", "1024"))


# ---------------------------------------------------------------- Milvus schema

def milvus_schema(client):
    """语料块集合：向量 + 标量元数据 + 倒排索引。"""
    from pymilvus import DataType
    schema = client.create_schema(auto_id=False, enable_dynamic_field=True)
    schema.add_field("chunk_id", DataType.VARCHAR, max_length=160, is_primary=True)
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=EMBED_DIM)
    schema.add_field("parent_id", DataType.VARCHAR, max_length=160)
    schema.add_field("doc_id", DataType.VARCHAR, max_length=160)
    schema.add_field("doc_title", DataType.VARCHAR, max_length=512)
    schema.add_field("doc_type", DataType.VARCHAR, max_length=48)
    schema.add_field("source_file", DataType.VARCHAR, max_length=512)
    schema.add_field("object_key", DataType.VARCHAR, max_length=512)   # MinIO 中的原件路径
    schema.add_field("section", DataType.VARCHAR, max_length=256)
    schema.add_field("chunk_type", DataType.VARCHAR, max_length=48)
    schema.add_field("category", DataType.VARCHAR, max_length=32)
    schema.add_field("platform", DataType.VARCHAR, max_length=32)
    schema.add_field("tier", DataType.VARCHAR, max_length=32)
    schema.add_field("deal_year", DataType.INT16)
    schema.add_field("char_len", DataType.INT16)
    schema.add_field("content", DataType.VARCHAR, max_length=16000)
    return schema


def milvus_index(client):
    idx = client.prepare_index_params()
    idx.add_index(field_name="embedding", index_type="HNSW", metric_type="COSINE",
                  params={"M": 16, "efConstruction": 200})
    for f in ("doc_type", "category", "platform", "tier", "chunk_type"):
        idx.add_index(field_name=f, index_type="INVERTED")
    return idx


# ---------------------------------------------------------------- 连接工厂

def milvus_client():
    from pymilvus import MilvusClient
    client = MilvusClient(uri=f"http://{MILVUS_HOST}:{MILVUS_PORT}")
    dbs = client.list_databases()
    if MILVUS_DB not in dbs:
        client.create_database(MILVUS_DB)
    client.use_database(MILVUS_DB)
    return client


def pg_connect(dbname: str | None = None):
    """连接 PostgreSQL。dbname=None 时连到默认库（用于建库）。"""
    import psycopg
    return psycopg.connect(host=PG_HOST, port=PG_PORT, user=PG_USER,
                           password=PG_PASSWORD, dbname=dbname or PG_DB, autocommit=True)


def pg_ensure_database() -> str:
    """确保项目库存在，返回实际库名。"""
    try:
        with pg_connect(PG_DB) as con:
            con.execute("SELECT 1")
        return PG_DB
    except Exception:
        with pg_connect("postgres") as con:
            con.execute(f'CREATE DATABASE "{PG_DB}"')
        return PG_DB


def minio_client(retries: int = 3, delay: float = 1.0, connect_timeout: float = 4.0):
    """MinIO 客户端。

    WSL2 端口转发偶发秒级抖动、MinIO 容器偶发无响应，这里：
    1. 给底层 urllib3 池设置短连接/读超时，避免单请求挂死调用线程；
    2. 对连接失败做短间隔重试，穿透瞬时抖动。
    """
    from minio import Minio
    from urllib3 import PoolManager
    import time as _time
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            http = PoolManager(timeout=connect_timeout, retries=1)
            cli = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY,
                        secret_key=MINIO_SECRET_KEY, secure=False,
                        http_client=http)
            return cli
        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                _time.sleep(delay)
    raise last_exc  # type: ignore[misc]


def minio_ensure_bucket() -> str:
    cli = minio_client()
    if not cli.bucket_exists(MINIO_BUCKET):
        cli.make_bucket(MINIO_BUCKET)
    return MINIO_BUCKET


# ---------------------------------------------------------------- 健康检查

def health() -> dict:
    """三组件连通性体检，供启动自检与故障排查使用。"""
    out: dict = {}

    try:
        c = milvus_client()
        out["milvus"] = {"ok": True, "db": MILVUS_DB, "collections": c.list_collections()}
        c.close()
    except Exception as e:
        out["milvus"] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}

    try:
        db = pg_ensure_database()
        with pg_connect(db) as con:
            ver = con.execute("SELECT version()").fetchone()[0]
            tables = [r[0] for r in con.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()]
        out["postgres"] = {"ok": True, "version": ver.split(",")[0], "db": db, "tables": tables}
    except Exception as e:
        out["postgres"] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}

    try:
        def _minio_check() -> dict:
            b = minio_ensure_bucket()
            cli = minio_client()
            objs = [o.object_name for o in cli.list_objects(b, recursive=True)]
            return {"ok": True, "endpoint": MINIO_ENDPOINT, "bucket": b,
                    "objects": len(objs)}
        with ThreadPoolExecutor(max_workers=1) as _pool:
            out["minio"] = _pool.submit(_minio_check).result(timeout=12)
    except Exception as e:
        out["minio"] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}",
                        "hint": "MinIO 无响应（容器可能僵死，docker restart starmoyu-minio）"}

    return out


def upload_object(local_path: Path | str, object_key: str | None = None) -> str:
    """把原件上传到 MinIO，返回 object_key。"""
    p = Path(local_path)
    key = object_key or p.name
    cli = minio_client()
    minio_ensure_bucket()
    cli.fput_object(MINIO_BUCKET, key, str(p))
    return key


def presigned_url(object_key: str, expires_seconds: int = 3600) -> str:
    """生成原件预签名直链（面试亮点：原件不经应用服务器中转）。"""
    import datetime
    cli = minio_client()
    return cli.presigned_get_object(MINIO_BUCKET, object_key,
                                    expires=datetime.timedelta(seconds=expires_seconds))


if __name__ == "__main__":
    import json
    print(json.dumps(health(), ensure_ascii=False, indent=2))
