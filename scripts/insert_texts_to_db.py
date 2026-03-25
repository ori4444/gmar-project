import os
import psycopg2
from datetime import datetime
from extract_texts import extract_from_text

# -----------------------------
# DB CONNECTION
# -----------------------------
conn = psycopg2.connect(
    "postgresql://postgres.bzrrkrqqsfiwejrjiijs:Ori1999cohen1@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres?sslmode=require"
)

cursor = conn.cursor()

# -----------------------------
# PATH
# -----------------------------
BASE_PATH = r"C:\Users\ONE1\Desktop\Gmar\data\raw_reports"


# -----------------------------
# DB HELPERS
# -----------------------------
def get_attack_id_by_date(attack_date):
    cursor.execute("""
        SELECT attack_id
        FROM attacks
        WHERE attack_date = %s
        LIMIT 1
    """, (attack_date,))

    res = cursor.fetchone()
    return res[0] if res else None


def insert_text(sentence, attack_id):
    cursor.execute("""
        INSERT INTO texts (attack_id, text)
        VALUES (%s, %s)
    """, (attack_id, sentence))


# -----------------------------
# MAIN
# -----------------------------
def process_all():
    total_inserted = 0

    for folder in os.listdir(BASE_PATH):
        folder_path = os.path.join(BASE_PATH, folder)

        if not os.path.isdir(folder_path):
            continue

        # -----------------------------
        # תאריך מהתיקייה
        # -----------------------------
        try:
            attack_date = datetime.strptime(folder, "%Y-%m-%d").date()
        except:
            continue

        attack_id = get_attack_id_by_date(attack_date)

        if not attack_id:
            print(f"\n⚠️ No attack found for date: {attack_date}")
            continue

        # -----------------------------
        # אוסף משפטים לתיקייה הזאת בלבד
        # -----------------------------
        folder_sentences = []

        for file in os.listdir(folder_path):
            if not file.endswith(".txt"):
                continue

            file_path = os.path.join(folder_path, file)

            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()

            results = extract_from_text(text)

            for item in results:
                folder_sentences.append(item["sentence"])

        if not folder_sentences:
            continue

        # -----------------------------
        # PREVIEW לתיקייה ספציפית
        # -----------------------------
        print("\n==============================")
        print(f"📂 Folder date: {attack_date}")
        print(f"attack_id: {attack_id}")
        print(f"Sentences found: {len(folder_sentences)}")
        print("==============================\n")

        for i, sentence in enumerate(folder_sentences, 1):
            print(f"{i}. {sentence}\n")

        # -----------------------------
        # אישור לתיקייה הזו בלבד
        # -----------------------------
        confirm = input(
            f"Insert these into DB for {attack_date}? (y/n): ").strip().lower()

        if confirm != "y":
            print("⏭ Skipping...\n")
            continue

        # -----------------------------
        # INSERT
        # -----------------------------
        for sentence in folder_sentences:
            insert_text(sentence, attack_id)
            total_inserted += 1

        conn.commit()

        print(
            f"✅ Inserted {len(folder_sentences)} sentences for {attack_date}\n")

    print(f"\n🎯 TOTAL INSERTED: {total_inserted}")


# -----------------------------
# RUN
# -----------------------------
if __name__ == "__main__":
    process_all()
    cursor.close()
    conn.close()