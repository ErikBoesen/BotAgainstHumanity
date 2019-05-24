import os
import requests
from flask import Flask, request, render_template, redirect
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room, leave_room
import eventlet
from threading import Thread


app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_POOL_SIZE"] = 15
db = SQLAlchemy(app)
socketio = SocketIO(app)

PREFIX = "CAH "


def process_message(message):
    responses = []
    forename = message.name.split(" ", 1)[0]
    if message["sender_type"] == "user":
        if message.text.startswith(PREFIX):
            instructions = message.text[len(PREFIX):].strip().split(None, 1)
            command = instructions.pop(0).lower()
            query = instructions[0] if len(instructions) > 0 else ""
            """
            if command == "help":
                if query:
                    query = query.strip(PREFIX)
                    elif query in commands:
                        responses.append(PREFIX + query + ": " + commands[query].DESCRIPTION + f". Requires {commands[query].ARGC} argument(s).")
                    else:
                        responses.append("No such command.")
                else:
                    help_string = "--- Help ---"
                    help_string += "\nStatic commands: " + ", ".join([PREFIX + title for title in static_commands])
                    help_string += "\nTools: " + ", ".join([PREFIX + title for title in commands])
                    help_string += f"\n(Run `{PREFIX}help commandname` for in-depth explanations.)"
                    responses.append(help_string)
            """
    return responses


def reply(message, group_id):
    send(process_message(Message.from_groupme(message)), group_id)


@app.route("/", methods=["POST"])
def groupme_webhook():
    """
    Receive callback to URL when message is sent in the group.
    """
    # Retrieve data on that single GroupMe message.
    message = request.get_json()
    group_id = message["group_id"]
    # Begin reply process in a new thread.
    # This way, the request won't time out if a response takes too long to generate.
    Thread(target=reply, args=(message, group_id)).start()
    return "ok", 200


def send(message, group_id):
    """
    Reply in chat.
    :param message: text of message to send. May be a tuple with further data, or a list of messages.
    :param group_id: ID of group in which to send message.
    """
    # Recurse when sending multiple messages.
    if isinstance(message, list):
        for item in message:
            send(item, group_id)
        return
    this_bot = Bot.query.get(group_id)
    # Close session so it won't remain locked on database
    db.session.close()
    data = {
        "bot_id": this_bot.bot_id,
    }
    image = None
    if isinstance(message, tuple):
        message, image = message
    # TODO: this is lazy
    if message is None:
        message = ""
    if len(message) > MAX_MESSAGE_LENGTH:
        # If text is too long for one message, split it up over several
        for block in [message[i:i + MAX_MESSAGE_LENGTH] for i in range(0, len(message), MAX_MESSAGE_LENGTH)]:
            send(block, group_id)
        data["text"] = ""
    else:
        data["text"] = message
    if image is not None:
        data["picture_url"] = image
    # Prevent sending message if there's no content
    # It would be rejected anyway
    if data["text"] or data.get("picture_url"):
        response = requests.post("https://api.groupme.com/v3/bots/post", data=data)


@app.route("/")
def home():
    return render_template("index.html", static_commands=static_commands.keys(), commands=[(key, commands[key].DESCRIPTION) for key in commands])


def in_group(group_id):
    return db.session.query(db.exists().where(Bot.group_id == group_id)).scalar()


@app.route("/manager", methods=["GET", "POST"])
def manager():
    access_token = request.args["access_token"]
    if request.method == "POST":
        # Build and send bot data
        group_id = request.form["group_id"]
        bot = {
            "name": request.form["name"] or "Yalebot",
            "group_id": group_id,
            "avatar_url": request.form["avatar_url"] or "https://i.groupme.com/310x310.jpeg.1c88aac983ff4587b15ef69c2649a09c",
            "callback_url": "https://yalebot.herokuapp.com/",
            "dm_notification": False,
        }
        me = requests.get(f"https://api.groupme.com/v3/users/me?token={access_token}").json()["response"]
        result = requests.post(f"https://api.groupme.com/v3/bots?token={access_token}",
                               json={"bot": bot}).json()["response"]["bot"]
        group = requests.get(f"https://api.groupme.com/v3/groups/{group_id}?token={access_token}").json()["response"]

        # Store in database
        registrant = Bot(group_id, group["name"], result["bot_id"], me["user_id"], me["name"], access_token)
        db.session.add(registrant)
        db.session.commit()
    groups = requests.get(f"https://api.groupme.com/v3/groups?token={access_token}").json()["response"]
    bots = requests.get(f"https://api.groupme.com/v3/bots?token={access_token}").json()["response"]
    if os.environ.get("DATABASE_URL") is not None:
        groups = [group for group in groups if not Bot.query.get(group["group_id"])]
        bots = [bot for bot in bots if Bot.query.get(bot["group_id"])]
    return render_template("manager.html", access_token=access_token, groups=groups, bots=bots)


class Bot(db.Model):
    __tablename__ = "bots"
    group_id = db.Column(db.String(16), unique=True, primary_key=True)
    group_name = db.Column(db.String(50))
    bot_id = db.Column(db.String(26), unique=True)
    owner_id = db.Column(db.String(16))
    owner_name = db.Column(db.String(64))
    access_token = db.Column(db.String(32))

    def __init__(self, group_id, group_name, bot_id, owner_id, owner_name, access_token):
        self.group_id = group_id
        self.group_name = group_name
        self.bot_id = bot_id
        self.owner_id = owner_id
        self.owner_name = owner_name
        self.access_token = access_token


@app.route("/delete", methods=["POST"])
def delete_bot():
    data = request.get_json()
    access_token = data["access_token"]
    bot = Bot.query.get(data["group_id"])
    req = requests.post(f"https://api.groupme.com/v3/bots/destroy?token={access_token}", json={"bot_id": bot.bot_id})
    if req.ok:
        db.session.delete(bot)
        db.session.commit()
        return "ok", 200


@app.route("/cah", methods=["GET"])
def cah():
    access_token = request.args["access_token"]
    return render_template("cah.html")


@app.route("/cah/join")
def cah_join_redirect():
    return redirect("https://oauth.groupme.com/oauth/authorize?client_id=iEs9DrSihBnH0JbOGZSWK8SdsqRt0pUn8EpulL8Fia3rf6QM", code=302)


@socketio.on("cah_connect")
def cah_connect(data):
    access_token = data["access_token"]
    # TODO: DRY!!
    user = requests.get(f"https://api.groupme.com/v3/users/me?token={access_token}").json()["response"]
    user_id = user["user_id"]
    game = commands["cah"].get_user_game(user_id)

    joined = cah_ping(access_token, room=False)
    if joined:
        join_room(game.group_id)
        cah_ping(access_token, single=False)


def cah_ping(access_token, room=True, single=True):
    # TODO: These lines are repeated like three times what are you DOING
    # TODO: Clean this up in the morning when you're sane
    user = requests.get(f"https://api.groupme.com/v3/users/me?token={access_token}").json()["response"]
    user_id = user["user_id"]
    game = commands["cah"].get_user_game(user_id)
    if room:
        selection = [card for _, card in game.selection]
        emit("cah_ping", {"black_card": game.current_black_card,
                          "selection_length": len(selection),
                          "selection": selection if game.players_needed() == 0 else None},
             room=game.group_id)
    if single:
        if game is None:
            emit("cah_update_user", {"joined": False})
            return False
        player = game.players[user_id]
        is_czar = game.is_czar(user_id)
        emit("cah_update_user", {"joined": True,
                                 "is_czar": is_czar,
                                 "hand": player.hand,
                                 "score": len(player.won)})
        return True


@socketio.on("cah_selection")
def cah_selection(data):
    access_token = data["access_token"]
    user = requests.get(f"https://api.groupme.com/v3/users/me?token={access_token}").json()["response"]
    user_id = user["user_id"]
    game = commands["cah"].get_user_game(user_id)
    player = game.players[user_id]
    group_id = game.group_id
    if game.is_czar(user_id):
        card, player = game.czar_choose(data["card_index"])
        send("The Card Czar has selected \"{card}\" played by {name}, who now has a score of {score}.".format(card=card,
                                                                                                              name=player.name,
                                                                                                              score=len(player.won)), group_id)
        send("The next black card is \"{card}\" and {name} is now Czar.".format(card=game.current_black_card,
                                                                                name=player.name), group_id)
    else:
        permitted = game.player_choose(user_id, data["card_index"])
        remaining_players = game.players_needed()
        if permitted:
            send(f"{player.name} has played a card. {remaining_players} still need to play.", group_id)
    cah_ping(access_token)
