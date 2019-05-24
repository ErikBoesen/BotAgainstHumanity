class Message:
    def __init__(self, message):
        self.text = message["text"]
        self.user_id = user_id
        if time is None:
            self.time = datetime.now()
        elif type(time) == int:
            self.time = datetime.fromtimestamp(time)
        else:
            self.time = time
        self.name = name
        self.sender_type = sender_type
        self.group_id = group_id
        self.avatar_url = avatar_url
        print(self)

    def __repr__(self):
        return colored("{location} | {name}: {text}".format(location=self.get_location(),
                                                            name=self.name,
                                                            text=self.text), color)

    @classmethod
    def from_groupme(cls, message: dict):
        return cls(message,
                   text=message["text"],
                   user_id=message["user_id"],
                   name=message["name"],
                   sender_type=message["sender_type"],
                   group_id=message["group_id"],
