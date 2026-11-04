from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# 存放数据库的路径
SQLALCHEMY_DATABASE_URL = "sqlite:///./data/boxpilot.db"

# check_same_thread=False 是 SQLite 在 FastAPI 多线程环境下的必备参数
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# 获取数据库 Session 的依赖注入函数
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()