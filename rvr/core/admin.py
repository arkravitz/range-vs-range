"""
Admin Cmd class for interacting with API
"""
from cmd import Cmd
from rvr.core.api import APIError, API
from rvr.core.dtos import LoginRequest, ChangeScreennameRequest
from rvr.core import dtos
from rvr.db.dump import load, dump
from sqlalchemy.exc import IntegrityError, OperationalError

#pylint:disable=R0201,R0904,E1103

class AdminCmd(Cmd):
    """
    Cmd class to make calls to an API instance
    """
    def __init__(self):
        Cmd.__init__(self)
        self.api = API()
        
    def do_recreate_timeouts(self, _details):
        """
        Recreate timeouts that were accidentally lost via dump
        """
        # TODO: 0: They could be (somewhat) manually reconstructed
        # for each game:
        #   gather all hand history items
        #   look for a gap
        #   fill the gap with a timeout
    
    def do_createdb(self, _details):
        """
        Create the database
        """
        result = self.api.create_db()
        if result:
            print "Error:", result
        else:
            print "Database created"
            
    def do_initialise(self, _details):
        """
        Initialise a (created but empty) database
        """
        result = self.api.initialise_db()
        if result:
            print "Error:", result
        else:
            print "Database initialised"
        result = self.api.ensure_open_games()
        if result:
            print "Error:", result
        else:
            print "Open games refreshed."            
    
    def do_login(self, params):
        """
        Calls the login API function
        login(identity, email, screenname)
        """
        params = params.split(None, 2)
        if len(params) == 3:
            request = LoginRequest(identity=params[0],
                                   email=params[1],
                                   screenname=params[2])
        else:
            print "Need exactly 3 parameters."
            print "For more info, help login"
            return
        response = self.api.login(request)
        if isinstance(response, APIError):
            print "Error:", response
            return
        print "User is logged in, response %r" % \
            (response)

    def do_getuserid(self, details):
        """
        getuserid <screenname>
        shows userid by screenname
        """
        user = self.api.get_user_by_screenname(details)
        if user is None:
            print "No such user"
        else:
            print "'%s' has userid %s" % (user.screenname, user.userid)
    
    def do_rmuser(self, details):
        """
        rmuser <userid>
        deletes user <userid>
        """
        userid = int(details)
        response = self.api.delete_user(userid)
        if isinstance(response, APIError):
            print "Error:", response
        elif response:
            print "Deleted."
        else:
            print "Nothing happened."
    
    def do_getuser(self, details):
        """
        getuser <userid>
        shows user's login details
        """
        userid = int(details)
        response = self.api.get_user(userid)
        if isinstance(response, APIError):
            print "Error:", response
        else:
            print "userid='%s'\nidentity='%s'\nemail='%s'\nscreenname='%s'" %  \
                (response.userid, response.identity, response.email,
                 response.screenname)

    def do_changesn(self, details):
        """
        changesn <userid> <newname>
        changes user's screenname to <newname>
        """
        args = details.split(None, 1)
        userid = int(args[0])
        newname = args[1]
        req = ChangeScreennameRequest(userid, newname)
        result = self.api.change_screenname(req)
        if isinstance(result, APIError):
            print "Error:", result.description
        else:
            print "Changed userid %d's screenname to '%s'" %  \
                (userid, newname)

    def do_opengames(self, _details):
        """
        Display open games, their descriptions, and their registered users.
        """
        response = self.api.get_open_games()
        if isinstance(response, APIError):
            print response
            return
        print "Open games:"
        for details in response:
            print details

    def do_runninggames(self, _details):
        """
        Display running games, their descriptions, and their users.
        """
        result = self.api.get_running_games()
        if isinstance(result, APIError):
            print "Error:", result.description
            return
        print "Running games:"
        for details in result:
            print details

    def do_joingame(self, params):
        """
        joingame <userid> <gameid>
        registers <userid> in open game <gameid>
        """
        args = params.split()
        userid = int(args[0])
        gameid = int(args[1])
        result = self.api.join_game(userid, gameid)
        if isinstance(result, APIError):
            print "Error:", result.description
        elif result is None:
            print "Registered userid %d in open game %d" % (userid, gameid)
        else:
            print "Registered userid %d in open game %d" % (userid, gameid)
            print "Started running game %d" % (result,)

    def do_leavegame(self, params):
        """
        leavegame <userid> <gameid>
        unregisters <userid> from open game <gameid>
        """
        args = params.split()
        userid = int(args[0])
        gameid = int(args[1])
        result = self.api.leave_game(userid, gameid)
        if isinstance(result, APIError):
            print "Error:", result.description
        else:
            print "Unregistered userid %d from open game %d" % (userid, gameid)

    def do_usersgames(self, params):
        """
        usersgames <userid>
        show details of all games associated with userid
        """
        userid = int(params)
        result = self.api.get_user_running_games(userid)
        if isinstance(result, APIError):
            print "Error:", result.description  # pylint:disable=E1101
            return
        print "Running games:"
        for game in result.running_details:
            print game
        print "Finished games:"
        for game in result.finished_details:
            print game
            
    def do_act(self, params):
        """
        act <gameid> <userid> <fold> <passive> <aggressive> <total>
        perform an action in a game as a user
        """
        params = params.split()
        gameid = int(params[0])
        userid = int(params[1])
        fold = params[2]
        passive = params[3]
        aggressive = params[4]
        total = int(params[5])
        range_action = dtos.ActionDetails(
            fold_raw=fold,
            passive_raw=passive,
            aggressive_raw=aggressive,
            raise_total=total)
        response = self.api.perform_action(gameid, userid, range_action)
        if isinstance(response, APIError):
            print "Error:", response.description  # pylint:disable=E1101
            return
        # pylint:disable=E1103
        if response.is_fold:
            print "You folded."
        elif response.is_passive:
            print "You called."
        elif response.is_aggressive:
            print "You raised to %d." % (response.raise_total,)
        else:
            print "Oops. This isn't right."
            
    def do_update(self, _details):
        """
        The kind of updates a background process would normally do. Currently
        includes:
         - ensure there's exactly one empty open game of each situation.
        """
        result = self.api.ensure_open_games()
        if result:
            print "Error:", result
        else:
            print "Open games refreshed."            

    def do_handhistory(self, params):
        """
        handhistory <gameid> [<userid>]
        Display hand history for given game, from given user's perspective, if
        specified.
        """
        args = params.split(None, 1)
        gameid = int(args[0])
        if len(args) > 1:
            userid = int(args[1])
            result = self.api.get_private_game(gameid, userid)
        else:
            userid = None
            result = self.api.get_public_game(gameid)
        if isinstance(result, APIError):
            print "Error:", result.description  # pylint:disable=E1101
            return
        print result
        
    def do_analyse(self, details):
        """
        analyse [refresh]
        Run all analysis. If refresh, reanalyse everything. 
        """
        if details == "":
            result = self.api.run_pending_analysis()
        elif details == "refresh":
            result = self.api.reanalyse_all()
        else:
            print "Bad syntax. See 'help analyse'."
            return
        if isinstance(result, APIError):
            print "Error:", result.description
        else:
            print "Analysis run."
            
    def do_timeout(self, _details):
        """
        timeout
        Fold any players who have timed out.
        """
        result = self.api.process_timeouts()
        if isinstance(result, APIError):
            print "Error:", result.description
        else:
            print result, "timeouts processed."

    def do_dump(self, params):
        """
        dump { out | in }
        
        dump out pickles the database to db.pkl
        dump in unpickles it
        
        To restore a database from a db.pkl file:
        1. delete the database file (rvr.db)
        2. "createdb"
        3. "dump in"
        4. "initialise"
        
        The "initiialise" does things like refreshing open games, because open
        games are not dumped out by "dump out".
        """
        filename = 'db.pkl'
        if params == 'out':
            dump(filename)
            print "Successfully exported database to %s." % (filename,)
        elif params == 'in':
            try:
                load(filename)
            except IntegrityError as err:
                print "IntegrityError. Is the database empty?"
                print "Perhaps delete the database and try the 'createdb' command."  # pylint:disable=C0301
                print "Details:", err
                return
            except OperationalError as err:
                print "OperationalError. Does the database exist?"
                print "Perhaps try the 'createdb' command."
                print "Details:", err
                return
            print "Successfully read %s into database." % (filename,)
        else:
            print "Bad syntax. See 'help dump'."

    def do_exit(self, _details):
        """
        Close the admin interface
        """
        print "Goodbye."
        return True