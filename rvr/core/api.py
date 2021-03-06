"""
Core API for Range vs. Range backend.
"""
from rvr.db.creation import BASE, ENGINE, create_session
from rvr.db import tables
from rvr.core import dtos
from functools import wraps
import logging
from sqlalchemy.exc import IntegrityError
import traceback
from rvr.poker.handrange import deal_from_ranges, remove_board_from_range,  \
    ANYTHING, NOTHING
from rvr.poker.action import range_action_fits, calculate_current_options,  \
    PREFLOP, RIVER, FLOP,  \
    NEXT_ROUND, TOTAL_COMMUNITY_CARDS,\
    act_passive, act_fold, act_aggressive, finish_game, WhatCouldBe
from rvr.core.dtos import MAP_TABLE_DTO
from rvr.infrastructure.util import concatenate
from rvr.poker.cards import deal_cards, Card, RANKS_HIGH_TO_LOW,  \
    SUITS_HIGH_TO_LOW
from sqlalchemy.orm.exc import NoResultFound
from rvr.mail.notifications import notify_current_player, notify_first_player, \
    notify_finished
from rvr.analysis.analyse import AnalysisReplayer, already_analysed
from rvr.db.tables import AnalysisFoldEquity, RangeItem
import datetime

#pylint:disable=R0903,R0904

def exception_mapper(fun):
    """
    Converts database exceptions to APIError
    """
    @wraps(fun)
    def inner(*args, **kwargs):
        """
        Catch exceptions and return API.ERR_UNKNOWN
        """
        # pylint:disable=W0703
        try:
            return fun(*args, **kwargs)
        except Exception as ex:
            logging.info("Unhandled exception in API function: %r", ex)
            for line in traceback.format_exc().splitlines():
                logging.info(line)
            return API.ERR_UNKNOWN
    return inner

def api(fun):
    """
    Equivalent to:
        @exception_mapper
        @create_session
        
    Used to ensure exception_mapper and create_session are applied in the
    correct order.
    """
    @wraps(fun)
    @exception_mapper
    @create_session
    def inner(*args, **kwargs):
        """
        Additional functionality: rollback on error. That needs to be here
        because it needs knowledge of both self.session and APIError.
        """
        try:
            result = fun(*args, **kwargs)
            if isinstance(result, APIError):
                self = args[0]
                self.session.rollback()
            return result
        except:
            self = args[0]
            self.session.rollback()
            raise
    return inner

class APIError(object):
    """
    These objects will be returned by @exception_mapper
    """
    def __init__(self, description):
        self.description = description
    
    def __str__(self):
        return self.description

class API(object):
    """
    Core Range vs. Range API, which may be called by:
     - a website
     - an admin console or command line
     - a thick client
    """
    
    ERR_UNKNOWN = APIError("Internal error")
    ERR_NO_SUCH_USER = APIError("No such user")
    ERR_NO_SUCH_OPEN_GAME = APIError("No such open game")
    ERR_NO_SUCH_RUNNING_GAME = APIError("No such running game")
    ERR_DUPLICATE_SCREENNAME = APIError("Duplicate screenname")
    ERR_JOIN_GAME_ALREADY_IN = APIError("User is already registered")
    ERR_JOIN_GAME_GAME_FULL = APIError("Game is full")
    ERR_DELETE_USER_PLAYING = APIError("User is playing")
    ERR_USER_NOT_IN_GAME = APIError("User is not in the specified game")
    ERR_DUPLICATE_SITUATION = APIError("Duplicate situation")
    
    def __init__(self):
        self.session = None  # required for @create_session
    
    @exception_mapper
    def create_db(self):
        """
        Create and seed the database
        """
        #pylint:disable=R0201
        BASE.metadata.create_all(ENGINE)
    
    def _add_all_situations(self):
        """
        Add all situations
        """
        err = None
        err = err or self._add_situation(_create_hu())
        err = err or self._add_situation(_create_three())
        return err
    
    def _add_card_combo(self, higher_card, lower_card):
        """
        Add a RangeItem
        """
        item = RangeItem()
        item.higher_card = higher_card.to_mnemonic()
        item.lower_card = lower_card.to_mnemonic()
        self.session.add(item)
        try:
            self.session.commit()
            logging.debug("Added range item: %s, %s", item.higher_card,
                          item.lower_card)
        except IntegrityError:
            self.session.rollback()
            # No error, it's a singleton, it's there, we're happy.
    
    def _add_card_combos(self):
        """
        Populate table of RangeItem
        """
        deck1 = [Card(rank, suit)
                 for rank in RANKS_HIGH_TO_LOW
                 for suit in SUITS_HIGH_TO_LOW]
        deck2 = deck1[:]
        err = None
        for card1 in deck1:
            for card2 in deck2:
                if card1 > card2:
                    err = err or self._add_card_combo(higher_card=card1,
                                                      lower_card=card2)
        return err  
    
    @api
    def initialise_db(self):
        """
        Create initial data for database
        """
        self.session.commit()  # any errors are our own
                        
        # Each of these also commits.
        err = None
        err = err or self._add_card_combos()
        err = err or self._add_all_situations()
        return err
    
    @api
    def login(self, request):
        """
        1. Create or validate OpenID-based account
        inputs: identity, email, screenname
        outputs: existed, user_details
        """
        matches = self.session.query(tables.User)  \
            .filter(tables.User.identity == request.identity)  \
            .filter(tables.User.email == request.email).all()
        if matches:
            # return user from database
            user = matches[0]
            user.unsubscribed = False
            return dtos.LoginResponse.from_user(user, True)
        else:
            # create user in database
            user = tables.User()
            user.identity = request.identity
            user.email = request.email
            user.screenname = request.screenname
            user.unsubscribed = False
            self.session.add(user)
            try:
                self.session.flush()
            except IntegrityError:
                self.session.rollback()
                # special error if it's just screenname (most likely cause)
                matches = self.session.query(tables.User)  \
                    .filter(tables.User.screenname == request.screenname).all()
                if matches:
                    return self.ERR_DUPLICATE_SCREENNAME
                else:
                    raise
            logging.debug("Created user %d with screenname '%s'",
                          user.userid, user.screenname)
            return dtos.LoginResponse.from_user(user, False)
            
    @api
    def unsubscribe(self, identity):
        """
        Unsubscribe the user from further emails (until the log in again).
        """
        matches = self.session.query(tables.User)  \
            .filter(tables.User.identity == identity).all()
        if not matches:
            return self.ERR_NO_SUCH_USER
        user = matches[0]
        user.unsubscribed = True
            
    @api
    def change_screenname(self, request):
        """
        Change user's screenname
        """
        # "Player X" is reserved for userid X
        if request.screenname.startswith("Player ") and  \
                request.screenname != "Player %d" % (request.userid,):
            return self.ERR_DUPLICATE_SCREENNAME
        # Check for existing user with this screenname
        matches = self.session.query(tables.User)  \
            .filter(tables.User.screenname == request.screenname).all()
        if matches:
            return self.ERR_DUPLICATE_SCREENNAME
        try:
            self.session.query(tables.User)  \
                .filter(tables.User.userid == request.userid)  \
                .one().screenname = request.screenname
        except NoResultFound:
            return self.ERR_NO_SUCH_USER
    
    @api
    def get_user(self, userid):
        """
        Get user's LoginDetails
        """
        matches = self.session.query(tables.User)  \
            .filter(tables.User.userid == userid).all()
        if not matches:
            return self.ERR_NO_SUCH_USER
        user = matches[0]
        return dtos.DetailedUser(userid=user.userid,
                                 identity=user.identity,
                                 email=user.email,
                                 screenname=user.screenname)
    
    @api
    def delete_user(self, userid):
        """
        Delete user if not playing any games.
        """
        matches = self.session.query(tables.User)  \
            .filter(tables.User.userid == userid).all()
        if not matches:
            return self.ERR_NO_SUCH_USER
        user = matches[0]
        if user.rgps or user.fgps:
            return self.ERR_DELETE_USER_PLAYING
        for ogp in user.ogps:
            self.session.delete(ogp)
        self.session.delete(user)
        return True
    
    @api
    def get_user_by_screenname(self, screenname):
        """
        Return userid, screenname
        """
        matches = self.session.query(tables.User)  \
            .filter(tables.User.screenname == screenname).all()
        if matches:
            user = matches[0]
            return dtos.UserDetails.from_user(user)
        else:
            return None
    
    def _add_situation(self, dto):
        """
        Add a dtos.SituationDetails to the database, as a tables.Situation and
        associated tables.SituationPlayer objects.
        """
        situation = tables.Situation()
        situation.description = dto.description
        situation.participants = len(dto.players)
        situation.is_limit = dto.is_limit
        situation.big_blind = dto.big_blind
        situation.board_raw = dto.board_raw
        situation.current_round = dto.current_round
        situation.pot_pre = dto.pot_pre
        situation.increment = dto.increment
        situation.bet_count = dto.bet_count
        situation.current_player_num = dto.current_player
        self.session.add(situation)
        for order, player in enumerate(dto.players):
            child = tables.SituationPlayer()
            child.situation = situation
            child.order = order
            child.stack = player.stack
            child.contributed = player.contributed
            child.range_raw = player.range_raw
            child.left_to_act = player.left_to_act
            self.session.add(child)
        try:
            self.session.commit()
            logging.debug("Added situation: %s", dto.description)
        except IntegrityError:
            self.session.rollback()
            return self.ERR_DUPLICATE_SITUATION            
    
    @api
    def get_open_games(self):
        """
        2. Retrieve open games including registered users
        inputs: (none)
        outputs: List of open games. For each game, users in game, details of
                 game
        """
        all_open_games = self.session.query(tables.OpenGame).all()
        results = [dtos.OpenGameDetails.from_open_game(game)
                   for game in all_open_games]
        return results
    
    @api
    def get_running_games(self):
        """
        Retrieve running games including registered users
        input: (none)
        outputs: List of running games. For each game, users in game, details
                 of game
        """
        all_running_games = self.session.query(tables.RunningGame).all()
        results = [dtos.RunningGameSummary.from_running_game(game)
                   for game in all_running_games]
        return results
    
    @api
    def get_user_running_games(self, userid):
        """
        3. Retrieve user's games and their statuses
        inputs: userid
        outputs: list of user's games. each may be open game, running (not our
        turn), running (our turn), finished. no more details of each game.
        
        Note: we don't validate that userid is a real userid!
        """
        rgps = self.session.query(tables.RunningGameParticipant)  \
            .filter(tables.RunningGameParticipant.userid == userid).all()
        running_games = [dtos.RunningGameSummary.from_running_game(rgp.game)
                         for rgp in rgps if rgp.game.current_userid is not None]
        finished_games = [dtos.RunningGameSummary.from_running_game(rgp.game)
                          for rgp in rgps if rgp.game.current_userid is None]
        return dtos.UsersGameDetails(userid, running_games, finished_games)
    
    def _start_game(self, open_game, final_ogp):
        """
        Takes the id of a full OpenGame, creates a new RunningGame from it,
        deletes the original and returns the id of the new RunningGame.
        
        This gets called when a game fills up, so that we can immediately tell
        the user that the game was started, and the new running game's id.
        
        Because adding a user to an open game happens in the same context as
        starting the game here, it's not possible for an open game to be left
        full.
        
        Returns RunningGame object, because there is no game id, because the
        object hasn't been committed yet, so the database hasn't created the id
        yet.
        """
        situation = open_game.situation
        all_ogps = open_game.ogps + [final_ogp]
        running_game = tables.RunningGame()
        running_game.next_hh = 0
        # Maintain game ids from open games, essentially hijacking the
        # uniqueness of the gameid sequence in open game.
        running_game.gameid = open_game.gameid
        running_game.situation = situation
        # We have to calculate current userid in advance so we can flush.
        running_game.current_userid =  \
            all_ogps[situation.current_player_num].userid
        running_game.board_raw = situation.board_raw
        running_game.current_round = situation.current_round
        running_game.pot_pre = situation.pot_pre
        running_game.increment = situation.increment
        running_game.bet_count = situation.bet_count
        running_game.current_factor = 1.0
        running_game.last_action_time = datetime.datetime.utcnow()
        situation_players = situation.ordered_players()
        self.session.add(running_game)
        self.session.flush()  # get gameid from database
        map_to_range = {p: p.range for p in situation.players}
        player_to_dealt = deal_from_ranges(map_to_range, running_game.board)
        for order, (ogp, s_p) in enumerate(zip(all_ogps, situation_players)):
            # create rgps in the order they will act in future rounds
            rgp = tables.RunningGameParticipant()
            rgp.gameid = running_game.gameid
            rgp.userid = ogp.userid  # haven't loaded users, so just copy userid
            rgp.order = order
            rgp.stack = s_p.stack
            rgp.contributed = s_p.contributed
            rgp.range_raw = s_p.range_raw
            rgp.left_to_act = s_p.left_to_act
            rgp.folded = False
            rgp.cards_dealt = player_to_dealt[s_p]
            if situation.current_player_num == order:
                assert running_game.current_userid == ogp.userid
            self.session.add(rgp)
            self.session.flush()  # populate game
            # Note that we do NOT create a range history item for them,
            # it is implied.
        # TODO: REVISIT: check that this cascades to ogps
        self.session.delete(open_game)
        self._deal_to_board(running_game)  # also changes ranges
        notify_first_player(running_game, starter_id=final_ogp.userid)
        logging.debug("Started game %d", open_game.gameid)
        return running_game
    
    def _record_hand_history_item(self, game, item):
        """
        Create a GameHistoryBase, and add it and item to the session.
        """
        base = tables.GameHistoryBase()
        base.gameid = game.gameid
        base.order = game.next_hh
        base.time = datetime.datetime.utcnow()
        game.next_hh += 1
        item.gameid = base.gameid
        item.order = base.order
        self.session.add(base)
        self.session.add(item)
    
    def _record_action_result(self, rgp, action_result):
        """
        Record that this user's range action resulted in this actual action.
        """
        if action_result.is_terminate:
            raise ValueError("Only real actions are supported.")
        element = tables.GameHistoryActionResult()
        element.userid = rgp.userid
        element.is_fold = action_result.is_fold
        element.is_passive = action_result.is_passive
        element.is_aggressive = action_result.is_aggressive
        element.call_cost = action_result.call_cost
        element.raise_total = action_result.raise_total
        element.is_raise = action_result.is_raise
        self._record_hand_history_item(rgp.game, element)
        rgp.game.last_action_time = datetime.datetime.utcnow()
    
    def _record_rgp_range(self, rgp, range_raw):
        """
        Record that this user now has this range in this game, in the hand
        history.
        """
        element = tables.GameHistoryUserRange()
        element.userid = rgp.userid
        element.range_raw = range_raw
        self._record_hand_history_item(rgp.game, element)
        
    def _record_range_action(self, rgp, range_action, is_check, is_raise):
        """
        Record that this user has made this range-based action
        """
        element = tables.GameHistoryRangeAction()
        element.userid = rgp.userid
        element.fold_range = range_action.fold_range.description
        element.passive_range = range_action.passive_range.description
        element.aggressive_range = range_action.aggressive_range.description
        element.raise_total = range_action.raise_total
        element.is_check = is_check
        element.is_raise = is_raise
        self._record_hand_history_item(rgp.game, element)
        
    def _record_board(self, game):
        """
        Record board at street
        """
        element = tables.GameHistoryBoard()
        element.street = game.current_round
        element.cards = game.board_raw
        self._record_hand_history_item(game, element)
        
    def _record_timeoout(self, rgp):
        """
        Record timeout
        """
        element = tables.GameHistoryTimeout()
        element.user = rgp.user
        self._record_hand_history_item(rgp.game, element)

    @api
    def join_game(self, userid, gameid):
        """
        5. Join/start game we're not in
        inputs: userid, gameid
        outputs: - running game id if the game started
                 - otherwise nothing
        errors: - gameid doesn't exist
                - userid doesn't exist
                - game is full
                - user is already registered
        """
        # check error conditions
        games = self.session.query(tables.OpenGame)  \
            .filter(tables.OpenGame.gameid == gameid).all()
        if not games:
            return self.ERR_NO_SUCH_OPEN_GAME
        game = games[0]
        users = self.session.query(tables.User)  \
            .filter(tables.User.userid == userid).all()
        if not users:
            return self.ERR_NO_SUCH_USER
        user = users[0]
        if any(ogp.userid == userid for ogp in game.ogps):
            return self.ERR_JOIN_GAME_ALREADY_IN
        game.participants += 1
        if game.participants > game.situation.participants:
            return self.ERR_JOIN_GAME_GAME_FULL  # This can't happen.

        ogp = tables.OpenGameParticipant()
        ogp.gameid = game.gameid
        ogp.userid = user.userid
            
        # start game?
        start_game = game.participants == game.situation.participants
        if start_game:
            running_game = self._start_game(game, ogp)
        else:
            # add user to game
            self.session.add(ogp)
            running_game = None
            
        try:
            # This commits either the add or the start game. We do this so that
            # if it's going to fail, it fails before the ensure_open_games()
            # below. This is important so that there are never duplicate game
            # ids.
            self.session.commit()  # explicitly check that it commits okay
        except IntegrityError as _ex:
            # An error will occur if game no longer exists, or user no longer
            # exists, or user has already been added to game, or game has
            # already been filled, or problems with starting the game!
            self.session.rollback()
            # Complete fail, okay, here we just try again!
            running_game = self.join_game(userid, gameid)
    
        logging.debug("User %d joined game %d", userid, gameid)
    
        self.ensure_open_games()
    
        # it's committed, so we will have the id
        if running_game is not None:
            return running_game.gameid
        
    @api
    def leave_game(self, userid, gameid):
        """
        4. Leave/cancel game we're in
        inputs: userid, gameid
        outputs: (none)
        """
        # check error conditions
        games = self.session.query(tables.OpenGame)  \
            .filter(tables.OpenGame.gameid == gameid).all()
        if not games:
            return self.ERR_NO_SUCH_OPEN_GAME
        game = games[0]
        users = self.session.query(tables.User)  \
            .filter(tables.User.userid == userid).all()
        if not users:
            return self.ERR_NO_SUCH_USER
        for ogp in game.ogps:
            if ogp.userid == userid:
                self.session.delete(ogp)
                break
        else:
            return self.ERR_USER_NOT_IN_GAME
        game.participants -= 1        
        # I don't know why, but flush here causes ensure_open_games to fail.
        # Failure to merge appropriately?
        self.session.commit()
        logging.debug("User %d left game %d", userid, gameid)
        self.ensure_open_games()        

    ERR_NOT_USERS_TURN = APIError("It's not that user's turn.")
    ERR_INVALID_RAISE_TOTAL = APIError("Invalid raise total.")
    ERR_INVALID_RANGES = APIError("Invalid ranges or raise size.")

    def apply_action_result(self, game, rgp, action_result):
        """
        Change game and rgp state. Add relevant hand history items. Possibly
        finish hand.
        """
        if action_result.is_fold:
            act_fold(rgp)
        elif action_result.is_passive:
            act_passive(rgp, action_result.call_cost)
        elif action_result.is_aggressive:
            act_aggressive(game, rgp, action_result.raise_total)
        else:
            # terminate must not be passed here
            raise ValueError("Invalid action result")
        left_to_act = [p for p in game.rgps if p.left_to_act]
        remain = [p for p in game.rgps if not p.left_to_act and not p.folded]
        if len(left_to_act) == 1 and not remain:
            # BB got a walk, or everyone folded to the button postflop
            game.is_finished = True
        elif not left_to_act:
            if len(remain) == 1 or game.current_round == RIVER:
                # The last person in folded their entire range, or
                # We have a range-based showdown on the river.
                game.is_finished = True
            else:
                # Deal, change round, set new game state.
                self._finish_betting_round(game, remain)
        else:
            # Who's up next? And not someone named Who, but the pronoun.
            later = [p for p in left_to_act if p.order > rgp.order]
            earlier = [p for p in left_to_act if p.order < rgp.order]
            chosen = later if later else earlier
            next_rgp = min(chosen, key=lambda p: p.order)
            game.current_userid = next_rgp.userid
            logging.debug("Next to act in game %d: userid %d, order %d",
                          game.gameid, next_rgp.userid, next_rgp.order)
    
    def _deal_to_board(self, game):
        """
        Deal as many cards as are needed to bring the board up to the current
        round. Also remove these cards from players' ranges.
        """
        total = TOTAL_COMMUNITY_CARDS[game.current_round]
        current = len(game.board)
        excluded_cards = concatenate(rgp.cards_dealt for rgp in game.rgps)
        excluded_cards.extend(game.board)
        new_board = game.board + deal_cards(excluded_cards, total - current)
        game.board = new_board
        if total > current:
            self._record_board(game)
        for rgp in game.rgps:
            rgp.range = remove_board_from_range(rgp.range, game.board)
        # TODO: EQUITY PAYMENT: cards dealt to board
    
    def _finish_betting_round(self, game, remain):
        """
        Deal and such
        """
        # Note that the original setting of game state is not here,
        # it's directly in API. Perhaps it should be here. Perhaps it will...
        current_user = min(remain, key=lambda r: r.order)
        game.current_userid = current_user.userid
        game.current_round = NEXT_ROUND[game.current_round]
        self._deal_to_board(game)
        game.increment = game.situation.big_blind
        game.bet_count = 0
        for rgp in game.rgps:
            # We move the contributed money into the pot
            # Note: from everyone, not just from those who remain
            game.pot_pre += rgp.contributed
            rgp.contributed = 0
            if rgp.folded:
                continue
            rgp.left_to_act = True
    
    def _perform_action(self, game, rgp, range_action, current_options):
        """
        Inputs:
         - game, tables.RunningGame object, from database
         - rgp, tables.RunningGameParticipant object, == game.current_rgp
         - range_action, action to perform
         - current_options, options user had here (re-computed)
         
        Outputs:
         - An ActionResult, what *actually* happened
         
        Side effects:
         - Records range action in DB (purely copying input to DB)
         - Records action result in DB
         - Records resulting range for rgp in DB
         - Applied the resulting action to the game, i.e. things like:
           - removing folded player from game
           - putting chips in the pot
           - starting the next betting round, if betting round finishes
           - determining who is next to act, if still same betting round
         - If the game is finished:
           - flag that the game is finished
           
        It's as simple as that. Now we just need to do / calculate as described,
        but instead of redealing based on can_call and can_fold, we'll play out
        each option, and terminate only when all options are terminal.
        """
        self._record_range_action(rgp, range_action,
            current_options.can_check(), current_options.is_raise)
        what_could_be = WhatCouldBe(game, rgp, range_action, current_options)
        what_could_be.consider_all()
        action_result = what_could_be.calculate_what_will_be()
        if action_result.is_terminate:
            logging.debug("gameid %r, determined to terminate", game.gameid)
            game.is_finished = True
            rgp.left_to_act = False
        else:
            logging.debug("gameid %r, determined to continue", game.gameid)
            self._record_action_result(rgp, action_result)
            self._record_rgp_range(rgp, rgp.range_raw)
            self.apply_action_result(game, rgp, action_result)
        if game.is_finished:
            finish_game(game)
        notify_current_player(game)  # Notify them *after* action obviously.
        return action_result
    
    @api
    def perform_action(self, gameid, userid, range_action):
        """
        Performs range_action for specified user in specified game.
        
        Fails if:
         - game is not a running game with user in it
         - it's not user's turn
         - range_action does not sum to user's current range
         - range_action raise_total isn't appropriate
        """
        games = self.session.query(tables.RunningGame)  \
            .filter(tables.RunningGame.gameid == gameid).all()
        if not games:
            return self.ERR_NO_SUCH_RUNNING_GAME
        game = games[0]
        
        # check that they're in the game and it's their turn        
        if game.current_userid == userid:
            rgp = game.current_rgp
        else:
            return self.ERR_NOT_USERS_TURN
        current_options = calculate_current_options(game, rgp)
        # check that their range action is valid for their options + range
        is_valid, _err = range_action_fits(range_action, current_options,
                                           rgp.range)
        if not is_valid:
            logging.debug("perform_action failing for reason %r in gameid %r, "
                          + "userid %r, range_action %r",
                          _err, gameid, userid, range_action)
            return API.ERR_INVALID_RANGES
        result = self._perform_action(game, rgp, range_action, current_options)
        return result
        
    def _get_history_items(self, game, userid=None):
        """
        Returns a list of game history items (tables.GameHistoryBase with
        additional details from child tables), with private data only for
        <userid>, if specified.
        """
        # pylint:disable=W0142
        child_items = [self.session.query(table)
                       .filter(table.gameid == game.gameid).all()
                       for table in MAP_TABLE_DTO.keys()]
        all_child_items = sorted(concatenate(child_items),
                                 key=lambda c: c.order)
        child_dtos = [dtos.GameItem.from_game_history_child(child)
                      for child in all_child_items]
        return [dto for dto in child_dtos
                if game.is_finished or dto.should_include_for(userid)]

    def _get_analysis_items(self, game):
        """
        Returns an ordered list of analysis items form the game.
        """
        afes = self.session.query(AnalysisFoldEquity)  \
            .filter(AnalysisFoldEquity.gameid == game.gameid).all()
        return {afe.order: dtos.AnalysisItemFoldEquity.from_afe(afe)
                for afe in afes}
        
    def _get_game(self, gameid, userid=None):
        """
        Return game <gameid>. If <userid> is not None, return private data for
        the specified user. If the game is finished, return all private data.
        Analysis items are considered private data, because they include both
        players' ranges.
        """
        if userid is not None:
            users = self.session.query(tables.User)  \
                .filter(tables.User.userid == userid).all()
            if not users:
                return self.ERR_NO_SUCH_USER
        games = self.session.query(tables.RunningGame)  \
            .filter(tables.RunningGame.gameid == gameid).all()
        #    .filter(tables.RunningGame.current_userid != None)
        if not games:
            return self.ERR_NO_SUCH_RUNNING_GAME
        game = games[0]
        game_details = dtos.RunningGameDetails.from_running_game(game)
        history_items = self._get_history_items(game, userid)
        analysis_items = self._get_analysis_items(game)
        if game.current_userid is None:
            current_options = None
        else:
            current_options = calculate_current_options(game, game.current_rgp)
        return dtos.RunningGameHistory(game_details=game_details,
                                       history_items=history_items,
                                       analysis_items=analysis_items,
                                       current_options=current_options)

    @api
    def get_public_game(self, gameid):
        """
        7. Retrieve game history without current player's ranges
        inputs: gameid
        outputs: hand history populated with ranges iff finished
        """
        return self._get_game(gameid)
    
    @api
    def get_private_game(self, gameid, userid):
        """
        8. Retrieve game history with current player's ranges
        inputs: userid, gameid
        outputs: hand history partially populated with ranges for userid only
        """
        return self._get_game(gameid, userid)
    
    @api
    def ensure_open_games(self):
        """
        Ensure there is exactly one empty open game for each situation in the
        database.
        """
        for situation in self.session.query(tables.Situation).all():
            empty_open = self.session.query(tables.OpenGame)  \
                .filter(tables.OpenGame.situationid ==
                        situation.situationid)  \
                .filter(tables.OpenGame.participants == 0).all()
            if len(empty_open) > 1:
                # delete all except one
                for game in empty_open[:-1]:
                    logging.debug("Deleted open game %d for situation %d",
                                  game.gameid, situation.situationid)
                    self.session.delete(game)
            elif len(empty_open) == 0:
                # add one!
                new_game = tables.OpenGame()
                new_game.situationid = situation.situationid
                new_game.participants = 0
                self.session.add(new_game)
                self.session.flush()  # get gameid
                logging.debug("Created open game %d for situation %d",
                              new_game.gameid, situation.situationid)
                
    def _run_pending_analysis(self):
        """
        Look through all games for analysis that has not yet been done, and do
        it, and record the analysis in the database.
        
        If you need to RE-analyse the database, delete existing analysis first. 
        """
        games = self.session.query(tables.RunningGame)  \
            .filter(tables.RunningGame.current_userid == None).all()
        for game in games:
            if not already_analysed(self.session, game):
                replayer = AnalysisReplayer(self.session, game)
                replayer.analyse()
                if already_analysed(self.session, game):
                    # Don't tell them if there's no analysis!
                    logging.debug("gameid %d, notifying", game.gameid)
                    notify_finished(game)

    @api
    def run_pending_analysis(self):
        """
        Analyse games that haven't been analysed.
        """
        return self._run_pending_analysis()

    @api
    def reanalyse_all(self):
        """
        Delete all analysis, and reanalyse all games.
        """
        self.session.query(tables.AnalysisFoldEquityItem).delete()
        self.session.query(tables.AnalysisFoldEquity).delete()
        self.session.commit()
        return self._run_pending_analysis()

    def _timeout(self, game):
        """
        Timeout the current player by folding their current range.
        """
        rgp = game.current_rgp
        current_options = calculate_current_options(game, rgp)
        range_action = dtos.ActionDetails(fold_raw=rgp.range_raw,
            passive_raw=NOTHING, aggressive_raw=NOTHING, raise_total=0)
        logging.debug("gameid %d, userid %d being timed out", game.gameid,
                      rgp.userid)
        self._record_timeoout(rgp)
        self._perform_action(game, rgp, range_action, current_options)

    @api
    def process_timeouts(self):
        """
        Fold players' hands where those players have not acted for the
        standard timeout time period.
        """
        count = 0
        games = self.session.query(tables.RunningGame)  \
            .filter(tables.RunningGame.current_userid != None).all()
        for game in games:
            if game.last_action_time + datetime.timedelta(days=7) <  \
                    datetime.datetime.utcnow():
                # This doesn't cause a race condition because we have isolation
                # level set to SERIALIZABLE
                self._timeout(game)
                count += 1
        return count

def _create_hu():
    """
    Create the heads-up situation
    """
    hu_bb = dtos.SituationPlayerDetails(  # pylint:disable=C0103
        stack=198,
        contributed=2,
        left_to_act=True,
        range_raw=ANYTHING)
    hu_btn = dtos.SituationPlayerDetails(
        stack=199,
        contributed=1,
        left_to_act=True,
        range_raw=ANYTHING)
    hu_situation = dtos.SituationDetails(
        description="Heads-up preflop, 100 BB",
        players=[hu_bb, hu_btn],  # BB acts first in future rounds
        current_player=1,  # BTN acts next (this round)
        is_limit=False,
        big_blind=2,
        board_raw='',
        current_round=PREFLOP,
        pot_pre=0,
        increment=2,
        bet_count=1)
    return hu_situation

def _create_three():
    """
    Create the three-handed situation
    """
    # pylint:disable=C0301
    three_co = dtos.SituationPlayerDetails(
        stack=195,
        contributed=0,
        left_to_act=True,
        range_raw="22+,A2s+,K7s+,Q9s+,J8s+,T8s+,97s+,87s,76s,65s,A8o+,KTo+,QTo+,JTo,T9o")
    three_btn = dtos.SituationPlayerDetails(
        stack=195,
        contributed=0,
        left_to_act=True,
        range_raw="88-22,AJs-A2s,KTs-K7s,Q9s,J9s,T8s+,97s+,86s+,75s+,64s+,54s,A8o+,KTo+,QJo")
    three_bb = dtos.SituationPlayerDetails(  # pylint:disable=C0103
        stack=195,
        contributed=0,
        left_to_act=True,
        range_raw="88-22,AJs-A2s,K7s+,Q9s+,J8s+,T7s+,96s+,86s+,75s+,64s+,54s,A8o+,KTo+,QTo+,J9o+,T9o,98o,87o")
    three_situation = dtos.SituationDetails(
        description="Three-way flop. CO minraised, BTN cold called, BB called. BB to act first on the flop.",
        players=[three_bb, three_co, three_btn],  # BB is first to act
        current_player=0,  # BB is next to act
        is_limit=False,
        big_blind=2,
        board_raw='',
        current_round=FLOP,
        pot_pre=16,
        increment=2,
        bet_count=0)
    return three_situation
