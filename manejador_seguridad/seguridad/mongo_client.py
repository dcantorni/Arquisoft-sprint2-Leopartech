import os
from pymongo import MongoClient

_client = None
_db = None

def get_mongo_db():
    global _client, _db
    if _db is None:
        mongo_uri = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/seguridad_logs')
        _client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        db_name = mongo_uri.split('/')[-1].split('?')[0]
        _db = _client[db_name]
    return _db


def ping_mongo():
    try:
        get_mongo_db().command('ping')
        return True
    except Exception:
        return False
