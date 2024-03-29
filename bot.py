import mebots
import os
import requests
from flask import Flask, request, render_template, redirect
from flask_socketio import SocketIO, emit, join_room, leave_room
import eventlet
from threading import Thread
import json
import random


bot = mebots.Bot('bah', os.environ['BOT_TOKEN'])
app = Flask(__name__)
socketio = SocketIO(app)

PREFIX = "cah"

games = {}
# TODO: use references to Player objects??
playing = {}


class Player:
    def __init__(self, user_id, name):
        self.user_id = user_id
        self.name = name
        self.hand = []
        self.won = []

    def draw_white(self, card):
        self.hand.append(card)

    def score(self, card):
        self.won.append(card)


class Game:
    def __init__(self, group_id):
        """
        Start game.

        :param group_id: ID of group in which to create game.
        """
        self.group_id = group_id
        self.players = {}
        self.selection = []
        self.hand_size = 8
        self.build_decks()
        self.czar_user_id = None
        self.draw_black_card()

    def build_decks(self):
        """
        Generate black and white decks.
        """
        self.build_black_deck()
        self.build_white_deck()

    def build_black_deck(self):
        """
        Read black cards from file.
        """
        with open("resources/black.json", "r") as f:
            self.black_deck = json.load(f)
        # Filter out Pick 2 cards for now
        self.black_deck = [card for card in self.black_deck if card.count("_") == 1]
        self.black_deck = [card.replace("_", "_" * 5) for card in self.black_deck]
        random.shuffle(self.black_deck)

    def build_white_deck(self):
        """
        Read white cards from file.
        """
        with open("resources/white.json", "r") as f:
            self.white_deck = json.load(f)
        random.shuffle(self.white_deck)

    def draw_black_card(self):
        """
        Choose a random new black card from deck.
        """
        self.current_black_card = self.black_deck.pop()

    def appoint_czar(self, user_id=None):
        """
        Change who's Card Czar.

        :param user_id: ID of user to appoint as Czar. If not provided, a random player will be chosen.
        """
        if user_id is None:
            user_id = random.choice(list(self.players.keys()))
        self.czar_user_id = user_id

    def join(self, user_id, name):
        """
        Add a player to the game.

        :param user_id: ID of user to add.
        :param name: the user's name.
        """
        if user_id in self.players:
            return False
        self.players[user_id] = Player(user_id, name)
        self.deal(user_id)
        if self.czar_user_id is None:
            self.appoint_czar(user_id)
        return True

    def deal(self, user_id):
        """
        Fill a user's hand.

        :param user_id: ID of user to deal to.
        """
        for i in range(self.hand_size):
            self.deal_one(user_id)

    def deal_one(self, user_id):
        """
        Deal one white card to a specified user.

        :param user_id: ID of user to whom to deal.
        """
        self.players[user_id].draw_white(self.white_deck.pop())

    def has_played(self, user_id) -> bool:
        """
        Check whether a user has played a card already this round.

        :param user_id: ID of user to check.
        :return: whether user has played.
        """
        for candidate_id, card in self.selection:
            if candidate_id == user_id:
                return True
        return False

    def player_choose(self, user_id, card_index) -> bool:
        """
        Take a card from a user's hand and play it for the round.

        :param user_id: ID of user who's playing.
        :param card_index: index of card that user has chosen in their hand or selection.
        :return: if the player was allowed to choose their card; i.e. if they hadn't already played.
        """
        if self.has_played(user_id):
            return False
        card = self.players[user_id].hand.pop(card_index)
        self.selection.append((user_id, card))
        self.deal_one(user_id)
        return True

    def players_needed(self) -> int:
        """
        Check how many players need to play before cards can be flipped and Czar can judge.

        :return: number of players who have not played a card yet this round, excluding Czar.
        """
        return len(self.players) - len(self.selection) - 1

    def is_czar(self, user_id) -> bool:
        """
        Check if a user is the Czar.

        :param user_id: ID of user to check.
        :return: whether user is Czar.
        """
        return self.czar_user_id == user_id

    def get_nth_card_user_id(self, n):
        """
        Get which user submitted the nth card in selection.
        Useful when Czar chooses a card and only the index is sent.

        :param n: index of chosen card.
        :return: ID of user who played that card.
        """
        # TODO: this relies on dictionaries staying in a static order, which they do NOT necessarily!
        # Use a less lazy implementation.
        counter = 0
        for user_id, card in self.selection:
            if counter == n:
                return user_id, card
            counter += 1
        return None, None

    def czar_choose(self, card_index):
        """
        Choose the winner of a round.

        :param card_index: index of the card the Czar selected.
        :return: Text of card played, and the Player who played it.
        """
        user_id, card = self.get_nth_card_user_id(card_index)
        self.players[user_id].score(self.current_black_card)
        self.draw_black_card()
        self.selection = []
        self.appoint_czar(user_id)
        # Return card and winner
        return card, self.players[user_id]


def get_user_game(user_id):
    game_group_id = playing.get(user_id)
    if game_group_id is None:
        return None
    return games[game_group_id]


def process_message(message):
    print(message)
    if message["sender_type"] == "user":
        if message["text"].lower().startswith(PREFIX):
            instructions = message["text"][len(PREFIX):].strip().split(None, 1)
            try:
                command = instructions.pop(0).lower()
            except IndexError:
                command = None
            query = instructions[0] if len(instructions) > 0 else ""
            group_id = message.get("group_id")
            user_id = message.get("user_id")
            name = message.get("name")

            game = games.get(group_id)
            if command in (None, "", "start"):
                if game:
                    return "Game already started!"
                game = Game(group_id)
                games[group_id] = game
                # TODO: DRY
                playing[user_id] = group_id
                game.join(user_id, name)
                return (f"Cards Against Humanity game started. {name} added to game as first Czar. Play at https://botagainsthumanitygroupme.herokuapp.com/play.\n"
                        "Other players can say 'CAH join' to join. 'CAH end' will terminate the game.\n")
            elif command == "end":
                if game is None:
                    return "No game in progress."
                games.pop(group_id)
                for user_id in game.players:
                    playing.pop(user_id)
                return "Game ended. Say 'CAH start' to start a new game."
            elif command == "join":
                if user_id in playing:
                    return "You're already in a game."
                if group_id not in games:
                    return "No game in progress. Say 'CAH start' to start a game."
                # TODO: DRY
                playing[user_id] = group_id
                game.join(user_id, name)
                return f"{name} has joined the game! Please go to https://botagainsthumanitygroupme.herokuapp.com/play to play."
            elif command == "leave":
                if user_id in playing:
                    playing.pop(user_id)
                    # TODO: remove them from game also!!
                    # TODO: need to make sure they weren't czar or anything.
                    return f"Removed {name} from the game."
                else:
                    return f"{name} is not currently in a game."
            elif command == "info":
                return str(games) + " " + str(playing) + " " + str(self)
            elif command == "help":
                return "Say 'CAH start' to start a game!"
            """
            elif command == "refresh":
                games[group_id].refresh(user_id)
            """
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


def api_get(endpoint, access_token):
    return requests.get(f"https://api.groupme.com/v3/users/{endpoint}?token={access_token}").json()["response"]


def get_me(access_token):
    return api_get("me", access_token)


def reply(message, bot_id, group_id):
    """
    Calculate message response, then send any response to the group it came from.
    Designed to be run in a thread.

    :param message: dictionary of message data received from webhook.
    :param bot_id: ID of bot to use to reply to message.
    :param group_id: ID of group in which message was sent.
    """
    send(process_message(message), bot_id, group_id)


@app.route("/message", methods=["POST"])
def receive_message_callback():
    """
    Receive callback to URL when message is sent in the group.
    """
    # Retrieve data on that GroupMe message.
    message = request.get_json()
    group_id = message["group_id"]
    bot_id = message.get("bot_id")
    # Begin reply process in a new thread.
    Thread(target=reply, args=(message, bot_id, group_id)).start()
    return "ok", 200


def send(message, bot_id, group_id):
    """
    Reply in chat.
    :param message: text of message to send.
    :param bot_id: ID of bot instance through which to send message.
    """
    if message:
        if bot_id is None:
            bot_id = bot.instance(group_id).id
        data = {
            "bot_id": bot_id,
            "text": message,
        }
        response = requests.post("https://api.groupme.com/v3/bots/post", json=data)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/play", methods=["GET"])
def cah():
    access_token = request.args.get("access_token")
    # If user attempted to join without an access_token URL parameter, redirect them to sign in with GroupMe and get one.
    if access_token is None:
        return redirect("https://oauth.groupme.com/oauth/authorize?client_id=09Mz3rJbvYCHQe6TV1022MKgSVtTFa4RYuw1t4bMxlD2hP6X", code=302)
    return render_template("play.html")


@socketio.on("game_connect")
def game_connect(data):
    access_token = data["access_token"]
    # TODO: DRY!!
    user = get_me(access_token)
    user_id = user["user_id"]
    game = get_user_game(user_id)

    joined = game_ping(access_token, room=False)
    if joined:
        join_room(game.group_id)
        game_ping(access_token, single=False)


def game_ping(access_token, room=True, single=True):
    # TODO: These lines are repeated like three times what are you DOING
    # TODO: Clean this up in the morning when you're sane
    user = get_me(access_token)
    user_id = user["user_id"]
    game = get_user_game(user_id)
    if room:
        selection = [card for _, card in game.selection]
        emit("game_ping", {"black_card": game.current_black_card,
                           "selection_length": len(selection),
                           "selection": selection if game.players_needed() == 0 else None},
             room=game.group_id)
    if single:
        if game is None:
            emit("game_update_user", {"joined": False})
            return False
        player = game.players[user_id]
        is_czar = game.is_czar(user_id)
        emit("game_update_user", {"joined": True,
                                  "is_czar": is_czar,
                                  "hand": player.hand,
                                  "score": len(player.won)})
        return True


@socketio.on("game_selection")
def game_selection(data):
    access_token = data["access_token"]
    user = get_me(access_token)
    user_id = user["user_id"]
    game = get_user_game(user_id)
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
    game_ping(access_token)


print("Loaded!")

if __name__ == "__main__":
    while True:
        print(process_message({"text": input("> "),
                               "sender_type": "user"}))
