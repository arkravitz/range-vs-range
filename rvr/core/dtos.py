"""
Data transfer objects:
- login, with OpenID, OpenID identity, email address, screenname
- user, with userid for user, system generated
- gameid for open, running or finished game
- range-based action
- open game, with list of users registered and details of situation
- general game, with status (open/running/finished), whose turn?, details of
  situation as per open game list
- hand history(!)
"""

class LoginDetails(object):
    """
    OpenID identity, email address, screenname
    """
    def __init__(self, userid, identity, email, screenname):
        self.userid = userid
        self.identity = identity
        self.email = email
        self.screenname = screenname

class UserDetails(object):
    """
    userid, screenname
    """
    def __init__(self, userid, screenname):
        self.userid = userid
        self.screenname = screenname
        
    @classmethod
    def from_user(cls, user):
        return cls(user.userid, user.screenname)

class RangeBasedActionDetails(object):
    """
    range-based action
    """
    pass

class OpenGameDetails(object):
    """
    list of users in game, and details of situation
    """
    def __init__(self, gameid, users, description):
        self.gameid = gameid
        self.users = users
        self.description = description

    def __str__(self):
        return "Open game %d, users: %s, description: %s" %  \
            (self.gameid, [u.screenname for u in self.users],
             self.description)
    
    @classmethod
    def from_open_game(cls, open_game):
        users = [UserDetails.from_user(o.user) for o in open_game.ogps]
        description = open_game.situation.description
        return cls(open_game.gameid, users, description)

class RunningGameDetails(object):
    """
    list of users in game, and details of situation
    """
    def __init__(self, gameid, users, description, user_details):
        self.gameid = gameid
        self.users = users
        self.description = description
        self.current_user_details = user_details

    def __str__(self):
        return "Running game %d, users: %s, description: %s, current: %s" %  \
            (self.gameid, [u.screenname for u in self.users],
             self.description, self.current_user_details.screenname)
    
    @classmethod
    def from_running_game(cls, running_game):
        users = [UserDetails.from_user(r.user) for r in running_game.rgps]
        description = running_game.situation.description
        user_details = UserDetails.from_user(running_game.current_user)
        return cls(running_game.gameid, users, description, user_details)

class FinishedGameDetails(object):
    """
    list of users in game, and details of situation
    """
    def __init__(self, gameid, users, description):
        self.gameid = gameid
        self.users = users
        self.description = description

    def __str__(self):
        return "Finished game %d, users: %s, description: %s" %  \
            (self.gameid, [u.screenname for u in self.users],
             self.description)
    
    @classmethod
    def from_finished_game(cls, finished_game):
        users = [UserDetails.from_user(f.user) for f in finished_game.fgps]
        description = finished_game.situation.description
        return cls(finished_game.gameid, users, description)

class UsersGameDetails(object):
    """
    lists of open game details, running game details, finished game details, for
    a specific user
    """
    def __init__(self, userid, running_details, finished_details):
        self.userid = userid
        self.running_details = running_details
        self.finished_details = finished_details

class GameDetails(object):
    """
    general game, with status (open/running/finished), whose turn?, details of
    situation as per open game list
    """
    pass

class HandHistoryDetails(object):
    """
    sufficient to show a completed game, analysis etc. to user
    sufficient for the user to choose their new range-based action
    """
    pass