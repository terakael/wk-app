import os
import subprocess

import psycopg2
from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from flask_bcrypt import Bcrypt
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from psycopg2.extras import RealDictCursor

load_dotenv()


def get_secret(command_str):
    return subprocess.check_output(command_str.split()).decode().strip()


# Load configuration from environment variables
host = os.getenv("POSTGRES_HOST")
port = os.getenv("POSTGRES_PORT")
user = os.getenv("POSTGRES_USER")
database = os.getenv("POSTGRES_DATABASE")
password = get_secret(os.getenv("POSTGRES_PASSWORD_COMMAND"))
secret_key = get_secret(os.getenv("FLASK_SECRET_KEY_COMMAND"))
debug = os.getenv("FLASK_DEBUG", "False").lower() == "true"
flask_host = os.getenv("FLASK_HOST", "127.0.0.1")
flask_port = int(os.getenv("FLASK_PORT", "5000"))


def get_db_connection():
    return psycopg2.connect(
        f"postgresql://{user}:{password}@{host}:{port}/{database}",
        cursor_factory=RealDictCursor,
    )


class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

    @staticmethod
    def get(user_id):
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, username FROM users WHERE id = %s", (user_id,))
        user_data = cursor.fetchone()
        conn.close()
        if user_data:
            return User(user_data["id"], user_data["username"])
        return None

    @staticmethod
    def authenticate(username, password):
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, password_hash FROM users WHERE username = %s",
            (username,),
        )
        user_data = cursor.fetchone()
        if user_data and bcrypt.check_password_hash(
            user_data["password_hash"], password
        ):
            # Update last_login
            cursor.execute(
                "UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = %s",
                (user_data["id"],),
            )
            conn.commit()
            conn.close()
            return User(user_data["id"], user_data["username"])
        conn.close()
        return None


app = Flask(__name__)
app.secret_key = secret_key

bcrypt = Bcrypt(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)


@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("radicals"))
    return redirect(url_for("login"))


@app.route("/radicals")
@login_required
def radicals():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, character, character_image, meaning, level
        FROM radicals
        ORDER BY level, meaning
    """)

    radicals_list = cursor.fetchall()

    # Group by level
    radicals_by_level = {}
    for radical in radicals_list:
        level = radical["level"]
        if level not in radicals_by_level:
            radicals_by_level[level] = []
        radicals_by_level[level].append(radical)

    conn.close()
    return render_template("radicals.html", radicals_by_level=radicals_by_level)


@app.route("/radicals/<int:radical_id>")
@login_required
def radical_detail(radical_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get radical details
    cursor.execute(
        """
        SELECT character, character_image, meaning, mnemonic, mnemonic_image, url, level
        FROM radicals
        WHERE id = %s
    """,
        (radical_id,),
    )

    radical = cursor.fetchone()

    if not radical:
        return "Radical not found", 404

    # Get kanji that use this radical
    cursor.execute(
        """
        SELECT k.id, k.character, k.meaning, k.level
        FROM kanji k
        JOIN kanji_radicals kr ON k.id = kr.kanji_id
        WHERE kr.radical_id = %s
        ORDER BY k.level, k.character
    """,
        (radical_id,),
    )

    kanji_list = cursor.fetchall()

    conn.close()
    return render_template(
        "radical_detail.html", radical=radical, kanji_list=kanji_list
    )


@app.route("/kanji")
@login_required
def kanji():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT k.id, k.character, k.meaning, k.level,
               STRING_AGG(CASE WHEN kr.reading_type = 'on' THEN kr.reading_text END, ', ') as onyomi
        FROM kanji k
        LEFT JOIN kanji_readings kr ON k.id = kr.kanji_id
        GROUP BY k.id, k.character, k.meaning, k.level
        ORDER BY k.level, k.character
    """)

    kanji_list = cursor.fetchall()

    # Group by level
    kanji_by_level = {}
    for kanji in kanji_list:
        level = kanji["level"]
        if level not in kanji_by_level:
            kanji_by_level[level] = []
        kanji_by_level[level].append(kanji)

    conn.close()
    return render_template("kanji.html", kanji_by_level=kanji_by_level)


@app.route("/kanji/<int:kanji_id>")
@login_required
def kanji_detail(kanji_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get kanji details
    cursor.execute(
        """
        SELECT character, meaning, url, level
        FROM kanji
        WHERE id = %s
    """,
        (kanji_id,),
    )

    kanji = cursor.fetchone()

    if not kanji:
        return "Kanji not found", 404

    # Get readings
    cursor.execute(
        """
        SELECT reading_type, reading_text
        FROM kanji_readings
        WHERE kanji_id = %s
        ORDER BY reading_type, reading_text
    """,
        (kanji_id,),
    )

    readings = cursor.fetchall()

    # Get mnemonics
    cursor.execute(
        """
        SELECT mnemonic_type, content
        FROM kanji_mnemonics
        WHERE kanji_id = %s
        ORDER BY mnemonic_type
    """,
        (kanji_id,),
    )

    mnemonics = cursor.fetchall()

    # Get radicals that make up this kanji
    cursor.execute(
        """
        SELECT r.id, r.character, r.character_image, r.meaning, r.level
        FROM radicals r
        JOIN kanji_radicals kr ON r.id = kr.radical_id
        WHERE kr.kanji_id = %s
        ORDER BY r.level, r.meaning
    """,
        (kanji_id,),
    )

    radicals = cursor.fetchall()

    # Get vocabulary that uses this kanji
    cursor.execute(
        """
        SELECT v.id, v.character, v.primary_meaning, v.reading, v.level
        FROM vocabulary v
        JOIN vocab_kanji_composition vkc ON v.id = vkc.vocab_id
        WHERE vkc.kanji_id = %s
        ORDER BY v.level, v.character
    """,
        (kanji_id,),
    )

    vocabulary = cursor.fetchall()

    conn.close()
    return render_template(
        "kanji_detail.html",
        kanji=kanji,
        readings=readings,
        mnemonics=mnemonics,
        radicals=radicals,
        vocabulary=vocabulary,
    )


@app.route("/vocabulary")
@login_required
def vocabulary():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, character, primary_meaning, reading, level
        FROM vocabulary
        ORDER BY level, character
    """)

    vocab_list = cursor.fetchall()

    # Group by level
    vocab_by_level = {}
    for vocab in vocab_list:
        level = vocab["level"]
        if level not in vocab_by_level:
            vocab_by_level[level] = []
        vocab_by_level[level].append(vocab)

    conn.close()
    return render_template("vocabulary.html", vocab_by_level=vocab_by_level)


@app.route("/test")
@login_required
def test():
    return render_template("test.html")


@app.route("/api/additional-info/<item_type>/<int:item_id>")
@login_required
def get_additional_info(item_type, item_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    info = {"meaning": "", "reading": ""}

    if item_type == "radical":
        cursor.execute(
            """
            SELECT character, character_image, meaning, mnemonic, mnemonic_image
            FROM radicals
            WHERE id = %s
        """,
            (item_id,),
        )
        result = cursor.fetchone()

        if result and result["mnemonic"]:
            info["meaning"] = result["mnemonic"]

    elif item_type == "kanji":
        # Get mnemonics by type
        cursor.execute(
            """
            SELECT mnemonic_type, content
            FROM kanji_mnemonics
            WHERE kanji_id = %s
            ORDER BY mnemonic_type
        """,
            (item_id,),
        )
        mnemonics = cursor.fetchall()

        for m in mnemonics:
            if m["mnemonic_type"] == "meaning":
                info["meaning"] = m["content"]
            elif m["mnemonic_type"] == "reading":
                info["reading"] = m["content"]

    elif item_type == "vocabulary":
        # Get explanations by type
        cursor.execute(
            """
            SELECT explanation_type, content
            FROM vocab_explanations
            WHERE vocab_id = %s
            ORDER BY explanation_type
        """,
            (item_id,),
        )
        explanations = cursor.fetchall()

        for e in explanations:
            if e["explanation_type"] == "meaning":
                info["meaning"] = e["content"]
            elif e["explanation_type"] == "reading":
                info["reading"] = e["content"]

    conn.close()

    return jsonify(info)


@app.route("/api/test-data")
@login_required
def get_test_data():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get 5 random radicals from level 1
    cursor.execute("""
        SELECT id, character, meaning
        FROM radicals
        WHERE level = 1
        ORDER BY RANDOM()
        LIMIT 5
    """)
    radicals = cursor.fetchall()

    # Get 5 random kanji with all readings from level 1
    cursor.execute("""
        SELECT k.id, k.character, k.meaning,
               STRING_AGG(kr.reading_text, ', ') as readings
        FROM kanji k
        LEFT JOIN kanji_readings kr ON k.id = kr.kanji_id
        WHERE k.level = 1
        GROUP BY k.id, k.character, k.meaning
        ORDER BY RANDOM()
        LIMIT 5
    """)
    kanji = cursor.fetchall()

    # Get 5 random vocabulary from level 1 with alternative meanings
    cursor.execute("""
        SELECT v.id, v.character, v.primary_meaning, v.reading,
               ARRAY_AGG(vam.meaning_text ORDER BY vam.meaning_text) as alternative_meanings
        FROM vocabulary v
        LEFT JOIN vocabulary_alternative_meanings vam ON v.id = vam.vocab_id
        WHERE v.level = 1
        GROUP BY v.id, v.character, v.primary_meaning, v.reading
        ORDER BY RANDOM()
        LIMIT 5
    """)
    vocabulary = cursor.fetchall()

    conn.close()

    # Build test items
    test_items = []

    # Add radicals
    for radical in radicals:
        test_items.append(
            {
                "id": f"radical_{radical['id']}",
                "type": "radical",
                "character": radical["character"] or radical["character_image"],
                "prompts": [{"type": "meaning", "answer": radical["meaning"]}],
                "mode": "review",
            }
        )

    # Add kanji
    for k in kanji:
        readings_list = [r.strip() for r in k["readings"].split(",") if r.strip()]
        test_items.append(
            {
                "id": f"kanji_{k['id']}",
                "type": "kanji",
                "character": k["character"],
                "prompts": [
                    {"type": "meaning", "answer": k["meaning"]},
                    {"type": "reading", "answer": readings_list},
                ],
                "mode": "review",
            }
        )

    # Add vocabulary
    for v in vocabulary:
        # Combine primary meaning with alternative meanings
        all_meanings = [v["primary_meaning"]]
        if v["alternative_meanings"]:
            all_meanings.extend([alt for alt in v["alternative_meanings"] if alt])

        test_items.append(
            {
                "id": f"vocab_{v['id']}",
                "type": "vocabulary",
                "character": v["character"],
                "prompts": [
                    {"type": "meaning", "answer": all_meanings},
                    {"type": "reading", "answer": v["reading"]},
                ],
                "mode": "review",
            }
        )

    # Add example learn items
    # Learn radicals
    for i in range(2):
        radical = radicals[i]
        test_items.append(
            {
                "id": f"learn_radical_{radical['id']}",
                "type": "radical",
                "character": radical["character"] or radical["character_image"],
                "prompts": [{"type": "meaning", "answer": radical["meaning"]}],
                "mode": "learn",
            }
        )

    # Learn kanji
    for i in range(2):
        k = kanji[i]
        readings_list = [r.strip() for r in k["readings"].split(",") if r.strip()]
        test_items.append(
            {
                "id": f"learn_kanji_{k['id']}",
                "type": "kanji",
                "character": k["character"],
                "prompts": [
                    {"type": "meaning", "answer": k["meaning"]},
                    {"type": "reading", "answer": readings_list},
                ],
                "mode": "learn",
            }
        )

    # Learn vocabulary
    for i in range(2):
        v = vocabulary[i]
        all_meanings = [v["primary_meaning"]]
        if v["alternative_meanings"]:
            all_meanings.extend([alt for alt in v["alternative_meanings"] if alt])
        test_items.append(
            {
                "id": f"learn_vocab_{v['id']}",
                "type": "vocabulary",
                "character": v["character"],
                "prompts": [
                    {"type": "meaning", "answer": all_meanings},
                    {"type": "reading", "answer": v["reading"]},
                ],
                "mode": "learn",
            }
        )

    # Items are now in order: radicals (5), kanji (5), vocabulary (5)
    # Each group is randomized within itself due to SQL ORDER BY RANDOM()

    return jsonify({"items": test_items})


@app.route("/vocabulary/<int:vocab_id>")
@login_required
def vocab_detail(vocab_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get vocabulary details
    cursor.execute(
        """
        SELECT character, primary_meaning, reading, url, level
        FROM vocabulary
        WHERE id = %s
    """,
        (vocab_id,),
    )

    vocab = cursor.fetchone()

    if not vocab:
        return "Vocabulary not found", 404

    # Get alternative meanings
    cursor.execute(
        """
        SELECT meaning_text
        FROM vocabulary_alternative_meanings
        WHERE vocab_id = %s
        ORDER BY meaning_text
    """,
        (vocab_id,),
    )

    alt_meanings = cursor.fetchall()

    # Get explanations
    cursor.execute(
        """
        SELECT explanation_type, content
        FROM vocab_explanations
        WHERE vocab_id = %s
        ORDER BY explanation_type
    """,
        (vocab_id,),
    )

    explanations = cursor.fetchall()

    # Get kanji composition
    cursor.execute(
        """
        SELECT k.id, k.character, k.meaning, k.level
        FROM kanji k
        JOIN vocab_kanji_composition vkc ON k.id = vkc.kanji_id
        WHERE vkc.vocab_id = %s
        ORDER BY k.level, k.character
    """,
        (vocab_id,),
    )

    kanji_composition = cursor.fetchall()

    conn.close()
    return render_template(
        "vocab_detail.html",
        vocab=vocab,
        alt_meanings=alt_meanings,
        explanations=explanations,
        kanji_composition=kanji_composition,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("radicals"))
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        user = User.authenticate(username, password)
        if user:
            login_user(user)
            return redirect(url_for("radicals"))
        else:
            flash("Invalid username or password")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(debug=debug, host=flask_host, port=flask_port)
