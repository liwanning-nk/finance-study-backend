import os
import sqlite3
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx

load_dotenv()
app = FastAPI(title="学情助教后端")

# 跨域白名单，已经写好你的GitHub Pages地址
ALLOW_ORIGINS = [
    "https://liwanning-nk.github.io",
    "https://liwanning-nk.github.io/emotion"
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

DB_PATH = "./study.db"
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS study_record (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT UNIQUE,
        pre_score REAL,
        rev_score REAL,
        others TEXT
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS quiz (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course TEXT,
        q TEXT,
        opts TEXT,
        ans INTEGER,
        tag TEXT,
        analysis TEXT
    )
    ''')
    cur.execute("SELECT COUNT(*) FROM quiz")
    cnt = cur.fetchone()[0]
    if cnt == 0:
        default_quiz = [
            {"course":"货币银行学","q":"基尼系数的取值范围？","opts":["0~1","0~10","1~100","-1~1"],"ans":0,"tag":"经济学","analysis":"基尼系数0是绝对平均，1是绝对不平均"},
            {"course":"货币银行学","q":"以下哪一项属于直接融资？","opts":["银行贷款","发行股票","存单","同业拆借"],"ans":1,"tag":"金融","analysis":"直接融资：资金供需直接对接，股票债券；间接融资有金融中介"}
        ]
        for item in default_quiz:
            cur.execute("INSERT INTO quiz(course,q,opts,ans,tag,analysis) VALUES (?,?,?,?,?,?)",
                (item["course"], item["q"], str(item["opts"]), item["ans"], item["tag"], item["analysis"]))
    conn.commit()
    conn.close()

init_db()

class RecordItem(BaseModel):
    date: str
    pre: float
    rev: float
    others: list[float]

class ChatReq(BaseModel):
    question: str

class QuizGenReq(BaseModel):
    text: str

@app.get("/api/study/records")
def get_records():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id,date,pre_score,rev_score,others FROM study_record ORDER BY date")
    rows = cur.fetchall()
    conn.close()
    res = []
    for r in rows:
        o = []
        if r[4]:
            o = [float(x) for x in r[4].split(",")]
        res.append({"id":r[0], "date":r[1], "pre":r[2], "rev":r[3], "others":o})
    return {"records": res}

@app.post("/api/study/record")
def add_record(item: RecordItem):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    others_str = ",".join(map(str, item.others))
    cur.execute('''
        INSERT OR REPLACE INTO study_record(date,pre_score,rev_score,others)
        VALUES (?,?,?,?)
    ''', (item.date, item.pre, item.rev, others_str))
    conn.commit()
    conn.close()
    return {"ok":True}

@app.delete("/api/study/record/{rid}")
def del_record(rid:int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM study_record WHERE id=?", (rid,))
    conn.commit()
    conn.close()
    return {"ok":True}

@app.get("/api/study/analysis")
def analysis():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT pre_score,rev_score FROM study_record")
    data = cur.fetchall()
    conn.close()
    if not data:
        return {"report":"暂无学情记录，添加分数后生成报告"}
    pre_list = [x[0] for x in data]
    rev_list = [x[1] for x in data]
    avg_pre = sum(pre_list)/len(pre_list)
    avg_rev = sum(rev_list)/len(rev_list)
    report = f"一共{len(data)}条学习记录。预习平均分：{avg_pre:.2f}，复习平均分：{avg_rev:.2f}。"
    if avg_rev >= avg_pre:
        report += "复习巩固效果不错，继续保持！"
    else:
        report += "复习分数低于预习，知识点复盘需要加强。"
    return {"report": report}

@app.get("/api/quiz/list")
def get_quiz(course:str=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if course and course != "all":
        cur.execute("SELECT id,course,q,opts,ans,tag,analysis FROM quiz WHERE course=?", (course,))
    else:
        cur.execute("SELECT id,course,q,opts,ans,tag,analysis FROM quiz")
    rows = cur.fetchall()
    conn.close()
    out = []
    import ast
    for r in rows:
        out.append({
            "id":r[0],"course":r[1],"q":r[2],
            "opts":ast.literal_eval(r[3]),"ans":r[4],"tag":r[5],"analysis":r[6]
        })
    return {"quiz":out}

@app.post("/api/quiz/generate")
async def gen_quiz(req: QuizGenReq):
    if not LLM_API_KEY:
        raise HTTPException(status_code=400, detail="未配置LLM_API_KEY，无法AI生成题目")
    prompt = f"""根据下面这段课件文本，生成1道单项选择题，输出JSON格式：
{{"q":"题目","opts":["A","B","C","D"],"ans":0,"analysis":"解析"}}
文本：{req.text}
只返回json，不要多余文字
"""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{LLM_BASE_URL}/chat/completions",
            headers={"Authorization":f"Bearer {LLM_API_KEY}", "Content-Type":"application/json"},
            json={
                "model":LLM_MODEL,
                "messages":[{"role":"user","content":prompt}],
                "temperature":0.3
            },
            timeout=30
        )
    j = resp.json()
    content = j["choices"][0]["message"]["content"].strip()
    import json
    qdata = json.loads(content)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO quiz(course,q,opts,ans,tag,analysis) VALUES (?,?,?,?,?,?)",
        ("自定义", qdata["q"], str(qdata["opts"]), qdata["ans"], "AI生成", qdata["analysis"]))
    conn.commit()
    conn.close()
    return qdata

@app.post("/api/chat")
async def chat(req: ChatReq):
    if not LLM_API_KEY:
        return {"answer":"⚠️ 后端没有配置大模型密钥，请在环境变量添加 LLM_API_KEY"}
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{LLM_BASE_URL}/chat/completions",
            headers={"Authorization":f"Bearer {LLM_API_KEY}", "Content-Type":"application/json"},
            json={
                "model":LLM_MODEL,
                "messages":[{"role":"user","content":req.question}],
                "temperature":0.4
            },
            timeout=40
        )
    j = resp.json()
    ans = j["choices"][0]["message"]["content"]
    return {"answer": ans}

@app.get("/")
def root():
    return {"msg":"✅ 学情助教后端正常运行，访问 /docs 查看接口文档"}