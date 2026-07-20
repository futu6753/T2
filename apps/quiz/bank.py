# -*- coding: utf-8 -*-
"""
@file    bank.py
@brief   题库(H02-E1):233 题五题型(单选 92/多选 34/判断 31/风险问答 40/
         看图识隐患 36)、84 张配图、四类底色(无色/黄/青/绿)分层;
         程序化确定性生成 + seed 幂等导入(INSERT OR IGNORE by qno);
         各题型判分规则集中于 grade()(风险问答按关键词命中)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json

TYPE_COUNTS = (("single", 92), ("multi", 34), ("judge", 31), ("risk", 40),
               ("image", 36))
COLORS = ("none", "yellow", "cyan", "green")   # 无色/黄/青/绿(分类刷题维度)
IMAGE_TOTAL = 84            # 看图识隐患 36 全配图 + 单选前 48 题配图 = 84

_TOPICS = ("配电室巡检", "高处作业", "受限空间作业", "临时用电", "动火作业",
           "起重吊装", "码头岸电", "变压器运维", "电缆敷设", "应急演练",
           "个人防护装备", "手持电动工具", "接地与接零", "防雷检测",
           "库房消防", "叉车作业", "脚手架搭设", "有毒有害气体", "触电急救",
           "工作票制度")

_SINGLE_OPTIONS = (
    ("立即停电并验电挂牌", "先干完手头活再处理", "口头告知即可作业",
     "由无证人员代为操作"),
    ("佩戴合格的绝缘防护用品", "赤手快速完成操作", "使用破损工具凑合",
     "跳过验电环节"),
    ("执行工作票与监护制度", "单人独自进入作业", "取消安全交底",
     "关闭现场警示标识"),
    ("先通风检测后进入", "直接进入抢时间", "点火照明查看", "无人监护作业"),
)
_MULTI_OPTIONS = (
    ("办理作业许可", "设置警戒隔离", "配备监护人员", "跳过风险辨识"),
    ("检查接地可靠", "确认灭火器材在位", "核对图纸与铭牌", "带电冲洗设备"),
    ("穿戴防护装备", "检测有害气体", "保持通讯畅通", "封闭全部出口"),
)
_RISK_KEYWORDS = (
    ("停电", "验电", "挂牌"), ("通风", "检测", "监护"),
    ("绝缘", "防护", "警示"), ("灭火", "疏散", "报警"),
    ("接地", "隔离", "许可"),
)


def _color_of(index: int) -> str:
    """@brief 底色确定性分层(全库循环)"""
    return COLORS[index % len(COLORS)]


def _build_one(qno: int, qtype: str, seq: int) -> dict:
    """@brief 生成一道确定性题目(seq=该题型内序号,从 0 起)"""
    topic = _TOPICS[(qno - 1) % len(_TOPICS)]
    color = _color_of(qno - 1)
    if qtype == "single":
        options = _SINGLE_OPTIONS[seq % len(_SINGLE_OPTIONS)]
        answer_index = seq % 4
        ordered = list(options[answer_index:]) + list(options[:answer_index])
        stem = f"【{topic}】第 {seq + 1} 题:下列做法中,正确的安全措施是?"
        image = f"assets/q{qno:03d}.png" if seq < 48 else ""
        return {"qno": qno, "qtype": qtype, "color": color, "stem": stem,
                "options": ordered, "answer": "ABCD"[ordered.index(
                    options[answer_index])],
                "analysis": f"{topic}作业须遵循先停电、后验电、再挂牌的顺序,"
                            f"并全程使用合格防护用品。",
                "image": image}
    if qtype == "multi":
        options = _MULTI_OPTIONS[seq % len(_MULTI_OPTIONS)]
        stem = f"【{topic}】第 {seq + 1} 题:开工前应当落实哪些措施?(多选)"
        return {"qno": qno, "qtype": qtype, "color": color, "stem": stem,
                "options": list(options), "answer": "ABC",
                "analysis": "前三项均为强制要求;最后一项属违章行为。",
                "image": ""}
    if qtype == "judge":
        truthy = seq % 2 == 0
        claim = ("作业前必须进行安全技术交底并履行签字确认。" if truthy
                 else "紧急情况下可以先作业后补办工作票。")
        return {"qno": qno, "qtype": qtype, "color": color,
                "stem": f"【{topic}】判断:{claim}",
                "options": ["对", "错"], "answer": "对" if truthy else "错",
                "analysis": "安全规程不允许任何形式的先作业后补票。",
                "image": ""}
    if qtype == "risk":
        keywords = _RISK_KEYWORDS[seq % len(_RISK_KEYWORDS)]
        return {"qno": qno, "qtype": qtype, "color": color,
                "stem": f"【{topic}】问答:发现该场景存在异常时,"
                        f"应采取的关键处置措施是什么?",
                "options": [], "answer": "|".join(keywords),
                "analysis": f"要点:{('、'.join(keywords))};答出任一要点即判对。",
                "image": ""}
    # image:看图识隐患(全部配图)
    return {"qno": qno, "qtype": qtype, "color": color,
            "stem": f"【{topic}】看图识隐患第 {seq + 1} 题:"
                    f"图中最主要的安全隐患是?",
            "options": ["未按规定佩戴防护装备", "现场布置完全合规",
                        "警示标识过多", "照明过于充足"],
            "answer": "A",
            "analysis": "图示人员未佩戴合格防护装备,属最主要隐患。",
            "image": f"assets/q{qno:03d}.png"}


def build_bank() -> list:
    """@brief 生成全量 233 题(确定性:同版本代码产物逐字节一致)"""
    bank = []
    qno = 1
    for qtype, count in TYPE_COUNTS:
        for seq in range(count):
            bank.append(_build_one(qno, qtype, seq))
            qno += 1
    return bank


def seed_bank(db) -> int:
    """@brief 幂等导入(qno 唯一键 OR IGNORE) @return 本次新插入条数"""
    before = db.query("SELECT COUNT(*) FROM quiz_questions")[0][0]
    for item in build_bank():
        db.execute(
            "INSERT OR IGNORE INTO quiz_questions(qno, qtype, color, stem,"
            " options_json, answer, analysis, image) VALUES(?,?,?,?,?,?,?,?)",
            (item["qno"], item["qtype"], item["color"], item["stem"],
             json.dumps(item["options"], ensure_ascii=False), item["answer"],
             item["analysis"], item["image"]))
    after = db.query("SELECT COUNT(*) FROM quiz_questions")[0][0]
    return after - before


def get_question(db, qno: int):
    """@brief 按 qno 取题 @return dict|None"""
    rows = db.query(
        "SELECT id, qno, qtype, color, stem, options_json, answer, analysis,"
        " image, difficulty FROM quiz_questions WHERE qno = ?", (qno,))
    if not rows:
        return None
    row = rows[0]
    return {"id": row[0], "qno": row[1], "qtype": row[2], "color": row[3],
            "stem": row[4], "options": json.loads(row[5]), "answer": row[6],
            "analysis": row[7], "image": row[8], "difficulty": row[9]}


def grade(question: dict, submitted: str) -> bool:
    """
    @brief  判分(做题模式):单选/看图=选项字母;多选=字母集合(顺序无关);
            判断=对/错;风险问答=命中任一关键词(H02-E1 判分规则)
    """
    answer = question["answer"]
    text = (submitted or "").strip()
    if question["qtype"] in ("single", "image", "judge"):
        return text.upper() == answer.upper() or text == answer
    if question["qtype"] == "multi":
        return set(text.upper()) == set(answer.upper()) and bool(text)
    keywords = answer.split("|")
    return any(keyword in text for keyword in keywords)
