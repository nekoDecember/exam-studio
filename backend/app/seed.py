from . import db
from .models import Question, Recipe


def seed():
    if db.get("meta", "seed"):
        return
    samples = [
        (
            "コンプライアンス",
            "choice",
            "業務中に顧客情報を含むメールを誤送信したことに気づきました。最初に取るべき行動として、最も適切なものを選んでください。",
            [
                "自分で解決するまで報告を控える",
                "速やかに上長・所定の窓口へ報告する",
                "送信履歴を削除する",
                "翌日の定例会議で共有する",
            ],
            "速やかに上長・所定の窓口へ報告する",
            "初動では速やかな報告が重要です。影響範囲の確認や対応は、所定の手順に沿って進めます。",
        ),
        (
            "マネジメント",
            "choice",
            "メンバーに業務を委任する際、最初に共有すべき内容はどれですか。",
            [
                "作業の目的・期待する成果・期限",
                "過去の失敗だけ",
                "上司の個人的な好み",
                "細部の手順だけ",
            ],
            "作業の目的・期待する成果・期限",
            "目的、成果、期限を共有することで、メンバーが判断しながら業務を進められます。",
        ),
        (
            "経営・財務",
            "word",
            "売上高から売上原価を差し引いた利益を何といいますか。",
            [],
            "売上総利益",
            "売上総利益＝売上高−売上原価です。粗利益とも呼ばれます。",
        ),
        (
            "マネジメント",
            "blank",
            "PDCAのCは、実行結果を（　）する段階です。",
            [],
            "評価",
            "Checkは結果を評価・検証する段階です。",
        ),
        (
            "コンプライアンス",
            "short",
            "顧客情報を社外へ持ち出す前に確認すべきことを、簡潔に説明してください。",
            [],
            "社内規程を確認し、必要な承認を得て、安全な管理方法を確保する。",
            "規程、承認、安全な管理方法の3点を確認しましょう。",
        ),
        (
            "経営・財務",
            "choice",
            "損益計算書が示すものはどれですか。",
            [
                "一定期間の経営成績",
                "特定時点の従業員数",
                "来年度の確定売上",
                "株主の住所",
            ],
            "一定期間の経営成績",
            "損益計算書は一定期間の収益・費用・利益を示します。",
        ),
    ]
    for i, (cat, typ, body, choices, answer, explanation) in enumerate(samples):
        q = Question(
            id=f"sample-{i + 1}",
            category=cat,
            question_type=typ,
            body=body,
            choices=choices,
            answer=answer,
            explanation=explanation,
            accepted_answers=["粗利益", "粗利"]
            if i == 2
            else ["検証", "確認"]
            if i == 3
            else [],
            warnings=[
                "一般的な学習用サンプルです。実際の社内規程を示すものではありません。"
            ],
            grading_rubric="規程の確認、承認、安全管理への言及",
            question_set_id="sample",
            parent=f"第{i // 2 + 1}問",
        ).model_dump()
        db.put("questions", q)
    db.put(
        "recipes",
        Recipe(
            id="sample-recipe", name="基本の選択問題", category="コンプライアンス"
        ).model_dump(),
    )
    for cat in ["コンプライアンス", "マネジメント", "経営・財務"]:
        db.put("categories", {"name": cat})
    db.put("meta", {"id": "seed"})
