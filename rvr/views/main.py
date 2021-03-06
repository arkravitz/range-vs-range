"""
The main pages for the site
"""
from flask import render_template, redirect, url_for
from rvr.app import APP
from rvr.forms.change import ChangeForm
from rvr.core.api import API, APIError
from rvr.app import AUTH
from rvr.core.dtos import LoginRequest, ChangeScreennameRequest,  \
    GameItemUserRange, GameItemBoard, GameItemActionResult,  \
    GameItemRangeAction, GameItemTimeout
import logging
from flask.helpers import flash
from flask.globals import request, session, g
from rvr.forms.action import action_form
from rvr.core import dtos
from rvr.poker.handrange import NOTHING, SET_ANYTHING_OPTIONS,  \
    HandRange, unweighted_options_to_description
from flask_googleauth import logout

# pylint:disable=R0911,R0912,R0914

def is_authenticated():
    """
    Is the user authenticated with OpenID?
    """
    return g.user and 'identity' in g.user

def is_logged_in():
    """
    Is the user logged in (i.e. they've been to the database)
    """
    return 'userid' in session and 'screenname' in session

def ensure_user():
    """
    Commit user to database and determine userid
    """
    # TODO: REVISIT: automagically hook this into AUTH.required 
    if not is_authenticated():
        # user is not authenticated yet
        return
    if is_logged_in():
        # user is authenticated and authorised (logged in)
        return
    if 'screenname' in session:
        # user had changed screenname but not yet logged in
        screenname = session['screenname']
    else:
        # regular login
        screenname = g.user['name']
    api = API()
    req = LoginRequest(identity=g.user['identity'],  # @UndefinedVariable
                       email=g.user['email'],  # @UndefinedVariable
                       screenname=screenname)
    result = api.login(req)
    if result == API.ERR_DUPLICATE_SCREENNAME:
        session['screenname'] = g.user['name']
        # User is authenticated with OpenID, but not yet authorised (logged
        # in). We redirect them to a page that allows them to choose a
        # different screenname.
        if request.endpoint != 'change_screenname':
            flash("The screenname '%s' is already taken." % (screenname,))
            return redirect(url_for('change_screenname'))
    elif isinstance(result, APIError):
        flash("Error registering user details.")
        logging.debug("login error: %s", result)
        return redirect(url_for('error_page'))
    else:
        # If their Google name is "Player <X>", they will not be able to have
        # their Google name as their screenname. Unless their login errors.
        session['userid'] = result.userid
        session['screenname'] = result.screenname
        req2 = ChangeScreennameRequest(result.userid,
                                       "Player %d" % (result.userid,))
        if not result.existed and result.screenname != req2.screenname:
            result2 = api.change_screenname(req2)
            if isinstance(result2, APIError):
                flash("Couldn't give you screenname '%s', "
                      "you are stuck with '%s' until you change it." %
                      (req2.screenname, result.screenname))
            else:
                session['screenname'] = req2.screenname
        flash("You have logged in as '%s'" % (session['screenname'],))

def error(message):
    """
    Flash error message and redirect to error page.
    """
    flash(message)
    return redirect(url_for('error_page'))

@APP.route('/change', methods=['GET','POST'])
@AUTH.required
def change_screenname():
    """
    Without the user being logged in, give the user the option to change their
    screenname from what Google OpenID gave us.
    """
    alt = ensure_user()
    if alt:
        return alt
    form = ChangeForm()
    if form.validate_on_submit():
        new_screenname = form.change.data
        if 'userid' in session:
            # Having a userid means they're in the database.
            req = ChangeScreennameRequest(session['userid'],
                                          new_screenname)
            resp = API().change_screenname(req)
            if resp == API.ERR_DUPLICATE_SCREENNAME:
                flash("That screenname is already taken.")
            elif isinstance(resp, APIError):
                logging.debug("change_screenname error: %s", resp)
                flash("An error occurred.")
            else:
                session['screenname'] = new_screenname
                flash("Your screenname has been changed to '%s'." %
                      (new_screenname, ))
                return redirect(url_for('home_page'))
        else:
            # User is not logged in. Changing screenname in session is enough.
            # Now when they go to the home page, ensure_user() will create their
            # account.
            session['screenname'] = new_screenname
            return redirect(url_for('home_page'))
    current = session['screenname'] if 'screenname' in session  \
        else g.user['name']
    navbar_items = [('', url_for('home_page'), 'Home'),
                    ('', url_for('about_page'), 'About'),
                    ('', url_for('faq_page'), 'FAQ')]
    return render_template('web/change.html', title='Change Your Screenname',
        current=current, form=form, navbar_items=navbar_items,
        is_logged_in=is_logged_in(), is_account=True)

@APP.route('/unsubscribe', methods=['GET'])
def unsubscribe():
    """
    Record that the user does not want to receive any more emails, at least
    until they log in again and in so doing clear that flag.
    
    Note that authentication is not required.
    """
    api = API()
    identity = request.args.get('identity', None)
    if identity is None:
        msg = "Invalid request, sorry."
    else:
        response = api.unsubscribe(identity)
        if response is api.ERR_NO_SUCH_USER:
            msg = "No record of you on Range vs. Range, sorry."
        elif isinstance(response, APIError):
            msg = "An unknown error occurred unsubscribing you, sorry."
        else:
            msg = "You have been unsubscribed. If you log in again, you will start receiving emails again."  # pylint:disable=C0301
    flash(msg)
    return render_template('web/flash.html', title='Unsubscribe')

@logout.connect_via(APP)
def on_logout(_source, **_kwargs):
    """
    I prefer to be explicit about what we remove on logout. 
    """
    session.pop('userid', None)
    session.pop('screenname', None)

@APP.route('/log-in', methods=['GET'])
@AUTH.required
def log_in():
    """
    Does what /login does, but in a way that I can get a URL for with url_for!
    """
    # TODO: REVISIT: get a relative URL for /login, instead of this.
    return redirect(url_for('home_page'))

@APP.route('/error', methods=['GET'])
def error_page():
    """
    Unauthenticated page for showing errors to user.
    """
    navbar_items = [('', url_for('home_page'), 'Home'),
                    ('', url_for('about_page'), 'About'),
                    ('', url_for('faq_page'), 'FAQ')]
    return render_template('web/flash.html', title='Sorry',
        navbar_items=navbar_items, is_logged_in=is_logged_in())

@APP.route('/about', methods=['GET'])
def about_page():
    """
    Unauthenticated information page.
    """
    navbar_items = [('', url_for('home_page'), 'Home'),
                    ('active', url_for('about_page'), 'About'),
                    ('', url_for('faq_page'), 'FAQ')]
    return render_template('web/about.html', title="About",
                           navbar_items=navbar_items,
                           is_logged_in=is_logged_in())

@APP.route('/faq', methods=['GET'])
def faq_page():
    """
    Frequently asked questions (unauthenticated).
    """
    navbar_items = [('', url_for('home_page'), 'Home'),
                    ('', url_for('about_page'), 'About'),
                    ('active', url_for('faq_page'), 'FAQ')]
    return render_template('web/faq.html', title="FAQ",
                           navbar_items=navbar_items,
                           is_logged_in=is_logged_in())

@APP.route('/', methods=['GET'])
def home_page():
    """
    Generates the unauthenticated landing page. AKA the main or home page.
    """
    if not is_authenticated():
        return render_template('web/landing.html')
    alt = ensure_user()
    if alt:
        return alt
    api = API()
    userid = session['userid']
    screenname = session['screenname']
    open_games = api.get_open_games()
    if isinstance(open_games, APIError):
        flash("An unknown error occurred retrieving your open games.")
        return redirect(url_for("error_page"))
    my_games = api.get_user_running_games(userid)
    if isinstance(my_games, APIError):
        flash("An unknown error occurred retrieving your running games.")
        return redirect(url_for("error_page"))
    selected_heading = request.cookies.get("selected-heading", "heading-open")
    my_games.running_details.sort(
        key=lambda rg: rg.current_user_details.userid != userid)
    my_open = [og for og in open_games
               if any([u.userid == userid for u in og.users])]
    others_open = [og for og in open_games
                   if not any([u.userid == userid for u in og.users])]
    form = ChangeForm()
    navbar_items = [('active', url_for('home_page'), 'Home'),
                    ('', url_for('about_page'), 'About'),
                    ('', url_for('faq_page'), 'FAQ')]
    return render_template('web/home.html', title='Home',
        screenname=screenname, userid=userid, change_form=form,
        r_games=my_games,
        my_open=my_open,
        others_open=others_open,
        my_finished_games=my_games.finished_details,
        navbar_items=navbar_items,
        selected_heading=selected_heading,
        is_logged_in=is_logged_in())

@APP.route('/join', methods=['GET'])
@AUTH.required
def join_game():
    """
    Join game, flash status, redirect back to /home
    """
    alt = ensure_user()
    if alt:
        return alt
    api = API()
    gameid = request.args.get('gameid', None)
    if gameid is None:
        flash("Invalid game ID.")
        return redirect(url_for('error_page'))
    try:
        gameid = int(gameid)
    except ValueError:
        flash("Invalid game ID.")
        return redirect(url_for('error_page'))
    userid = session['userid']
    response = api.join_game(userid, gameid)
    if response is api.ERR_JOIN_GAME_ALREADY_IN:
        msg = "You are already registered in game %s." % (gameid,)
    elif response is api.ERR_JOIN_GAME_GAME_FULL:
        msg = "Game %d is full." % (gameid,)
    elif response is api.ERR_NO_SUCH_OPEN_GAME:
        msg = "Invalid game ID."
    elif response is api.ERR_NO_SUCH_USER:
        msg = "Oddly, your account does not seem to exist. " +  \
            "Try logging out and logging back in."
        logging.debug("userid %d can't register for game %d, " +
                      "because user doesn't exist.",
                      userid, gameid)
    elif isinstance(response, APIError):
        msg = "An unknown error occurred joining game %d, sorry." % (gameid,)
        logging.debug("unrecognised error from api.join_game: %s", response)
    else:
        msg = "You have joined game %s." % (gameid,)
        flash(msg)
        return redirect(url_for('home_page'))
    flash(msg)
    return redirect(url_for('error_page'))

@APP.route('/leave', methods=['GET'])
@AUTH.required
def leave_game():
    """
    Leave game, flash status, redirect back to /home
    """
    alt = ensure_user()
    if alt:
        return alt
    api = API()
    gameid = request.args.get('gameid', None)
    if gameid is None:
        flash("Invalid game ID.")
        return redirect(url_for('error_page'))
    try:
        gameid = int(gameid)
    except ValueError:
        flash("Invalid game ID.")
        return redirect(url_for('error_page'))
    userid = session['userid']
    response = api.leave_game(userid, gameid)
    if response is api.ERR_USER_NOT_IN_GAME:
        msg = "You are not registered in game %s." % (gameid,)
    elif response is api.ERR_NO_SUCH_OPEN_GAME:
        msg = "Invalid game ID."
    elif isinstance(response, APIError):
        msg = "An unknown error occurred leaving game %d, sorry." % (gameid,)
    else:
        msg = "You have left game %s." % (gameid,)
        flash(msg)
        return redirect(url_for('home_page'))
    flash(msg)
    return redirect(url_for('error_page'))

def _handle_action(gameid, userid, api, form, can_check, can_raise):
    """
    Handle response from an action form
    """
    # pylint:disable=R0912,R0913
    fold = form.fold.data
    passive = form.passive.data
    aggressive = form.aggressive.data
    if aggressive != NOTHING:
        try:
            total = int(form.total.data)
        except ValueError:
            flash("Incomprehensible raise total.")
            return False
    else:
        total = 0
    range_action = dtos.ActionDetails(fold_raw=fold, passive_raw=passive,
                                      aggressive_raw=aggressive,
                                      raise_total=total)
    try:
        range_name = "Fold"
        range_action.fold_range.validate()
        range_name = "Check" if can_check else "Call"
        range_action.passive_range.validate()
        range_name = "Raise" if can_raise else "Bet"
        range_action.aggressive_range.validate()
    except ValueError as err:
        flash("%s range is invalid. Reason: %s." % (range_name, err.message))
        return False
    logging.debug("gameid %r, performing action, userid %r, range_action %r",
                  gameid, userid, range_action)
    result = api.perform_action(gameid, userid, range_action)
    # why do validation twice...
    if isinstance(result, APIError):
        if result is api.ERR_INVALID_RAISE_TOTAL:
            msg = "Invalid raise total."
        elif result is api.ERR_INVALID_RANGES:
            msg = "Invalid ranges for that action."
        else:
            msg = "An unknown error occurred performing action, sorry."
            logging.info('Unknown error from api.perform_action: %s', result)
        flash(msg)
        return False
    else:
        if result.is_fold:
            msg = "You folded."
        elif result.is_passive:
            if result.call_cost == 0:
                msg = "You checked."
            else:
                msg = "You called for %d." % (result.call_cost,)
        elif result.is_aggressive:
            if result.is_raise:
                msg = "You raised to %d." % (result.raise_total,)
            else:
                msg = "You bet %d." % (result.raise_total,)
        elif result.is_terminate:
            msg = "The game is finished."
        else:
            msg = "I can't figure out what happened, eh."
        flash(msg)
        return True

def _board_to_vars(item, _index):
    """
    Replace little images into it
    """
    cards = [item.cards[i:i+2] for i in range(0, len(item.cards), 2)]
    return item.street, cards

def _range_action_to_vars(item, index):
    """
    Convert to percentages (relative), and such.
    
    Returns (total_range, fold_range, passive_range, aggressive_range, username,
             fold_pct, passive_pct, aggressive_pct, raise_total)
    """
    fold_options = item.range_action.fold_range  \
        .generate_options_unweighted()
    passive_options = item.range_action.passive_range  \
        .generate_options_unweighted()
    aggressive_options = item.range_action.aggressive_range  \
        .generate_options_unweighted()
    all_options = fold_options + passive_options + aggressive_options
    combined_range = unweighted_options_to_description(all_options)
    fold_total = len(fold_options)
    passive_total = len(passive_options)
    aggressive_total = len(aggressive_options)
    total = len(all_options)
    # NOTE: some of this is necessarily common with ACTION_SUMMARY
    return {"screenname": item.user.screenname,
            "fold_pct": 100.0 * fold_total / total,
            "passive_pct": 100.0 * passive_total / total,
            "aggressive_pct": 100.0 * aggressive_total / total,
            "raise_total": item.range_action.raise_total,
            "is_check": item.is_check,
            "is_raise": item.is_raise,
            "original": combined_range,
            "fold": item.range_action.fold_range.description,
            "passive": item.range_action.passive_range.description,
            "aggressive": item.range_action.aggressive_range.description,
            "index": index}

def _action_summary_to_vars(range_action, action_result, action_result_index,
                            user_range, index):
    """
    Summarise an action result and user range in the context of the most recent
    range action.
    """
    new_total = len(HandRange(user_range.range_raw).  \
        generate_options_unweighted())
    fol = pas = agg = NOTHING
    if action_result.action_result.is_fold:
        original = fol = range_action.range_action.fold_range.description
    elif action_result.action_result.is_passive:
        original = pas = range_action.range_action.passive_range.description
    else:
        original = agg = range_action.range_action.aggressive_range.description
    # NOTE: some of this is necessarily common with RANGE_ACTION
    return {"screenname": user_range.user.screenname,
            "action_result": action_result.action_result,
            "action_result_index": action_result_index,
            "percent": 100.0 * new_total / len(SET_ANYTHING_OPTIONS),
            "combos": new_total,
            "is_check": range_action.is_check,
            "is_raise": range_action.is_raise,
            "original": original,
            "fold": fol,
            "passive": pas,
            "aggressive": agg,
            "index": index}
    
def _action_result_to_vars(action_result, index):
    """
    Summarises action result for the case where the hand is in progress, and
    the user is not allowed to view the other players' ranges.
    """
    return {"screenname": action_result.user.screenname,
            "action_result": action_result.action_result,
            "index": index}
    
def _timeout_to_vars(timeout, index):
    """
    Summarises a timeout.
    """
    return {"screenname": timeout.user.screenname,
            "index": index}

def _make_history_list(game_history):
    """
    Hand history items provide a basic to-text function. This function adds a
    little extra HTML where necessary, to make them prettier.
    """
    results = []
    # There is always a range action and an action before a user range
    most_recent_range_action = None
    most_recent_action_result = None
    pending_action_result = None
    for index, item in enumerate(game_history):
        if isinstance(item, GameItemUserRange):
            if pending_action_result is not None:
                results.remove(pending_action_result)
                action_result_index = pending_action_result[1]['index']
                pending_action_result = None
            results.append(("ACTION_SUMMARY",
                            _action_summary_to_vars(most_recent_range_action,
                                                    most_recent_action_result,
                                                    action_result_index,
                                                    item, index)))
        elif isinstance(item, GameItemRangeAction):
            most_recent_range_action = item
            results.append(("RANGE_ACTION",
                            _range_action_to_vars(item, index)))
        elif isinstance(item, GameItemActionResult):
            most_recent_action_result = item
            pending_action_result = ("ACTION_RESULT",
                                     _action_result_to_vars(item, index))
            results.append(pending_action_result)
            # This will be removed if there is a following user range
        elif isinstance(item, GameItemBoard):
            results.append(("BOARD", _board_to_vars(item, index)))
        elif isinstance(item, GameItemTimeout):
            results.append(("TIMEOUT", _timeout_to_vars(item, index)))
        else:
            logging.debug("unrecognised type of hand history item: %s", item)
            results.append(("UNKNOWN", (str(item),)))
    return results

def _running_game(game, gameid, userid, api):
    """
    Response from game page when the requested game is still running.
    """
    form = action_form(is_check=game.current_options.can_check(),
        is_raise=game.current_options.is_raise,
        can_raise=game.current_options.can_raise(),
        min_raise=game.current_options.min_raise,
        max_raise=game.current_options.max_raise)
    if form.validate_on_submit():
        if _handle_action(gameid, userid, api, form,
                          game.current_options.can_check(),
                          game.current_options.can_raise()):
            return redirect(url_for('game_page', gameid=gameid))   
    
    # First load, OR something's wrong with their data.
    range_editor_url = url_for('range_editor',
        rng_original=game.game_details.current_player.range_raw,
        board=game.game_details.board_raw,
        raised="true" if game.current_options.is_raise else "false",
        can_check="true" if game.current_options.can_check() else "false",
        can_raise="true" if game.current_options.can_raise() else "false",
        min_raise=game.current_options.min_raise,
        max_raise=game.current_options.max_raise)
    title = 'Game %d' % (gameid,)
    history = _make_history_list(game.history)
    board_raw = game.game_details.board_raw
    board = [board_raw[i:i+2] for i in range(0, len(board_raw), 2)]
    is_me = (userid == game.game_details.current_player.user.userid)
    navbar_items = [('', url_for('home_page'), 'Home'),
                    ('', url_for('about_page'), 'About'),
                    ('', url_for('faq_page'), 'FAQ')]
    return render_template('web/game.html', title=title, form=form,
        board=board, game_details=game.game_details, history=history,
        current_options=game.current_options,
        is_me=is_me, is_running=True,
        range_editor_url=range_editor_url,
        navbar_items=navbar_items, is_logged_in=is_logged_in())

def _finished_game(game, gameid):
    """
    Response from game page when the requested game is finished.
    """
    title = 'Game %d' % (gameid,)
    history = _make_history_list(game.history)
    analyses = game.analysis.keys() 
    navbar_items = [('', url_for('home_page'), 'Home'),
                    ('', url_for('about_page'), 'About'),
                    ('', url_for('faq_page'), 'FAQ')]
    return render_template('web/game.html', title=title,
        game_details=game.game_details, history=history, analyses=analyses,
        is_running=False,
        navbar_items=navbar_items, is_logged_in=is_logged_in())

def authenticated_game_page(gameid):
    """
    Game page when user is not authenticated (i.e. the public view)
    """
    alt = ensure_user()
    if alt:
        return alt
    userid = session['userid']

    api = API()
    response = api.get_private_game(gameid, userid)
    if isinstance(response, APIError):
        if response is api.ERR_NO_SUCH_RUNNING_GAME:
            msg = "Invalid game ID."
        else:
            msg = "An unknown error occurred retrieving game %d, sorry." %  \
                (gameid,)
        flash(msg)
        return redirect(url_for('error_page'))
    
    if response.is_finished():
        return _finished_game(response, gameid)
    else:
        return _running_game(response, gameid, userid, api)

def unauthenticated_game_page(gameid):
    """
    Game page when user is authenticated (i.e. the private view)
    """
    api = API()
    response = api.get_public_game(gameid)
    if isinstance(response, APIError):
        if response is api.ERR_NO_SUCH_RUNNING_GAME:
            msg = "Invalid game ID."
        else:
            msg = "An unknown error occurred retrieving game %d, sorry." %  \
                (gameid,)
        flash(msg)
        return redirect(url_for('error_page'))
    
    if response.is_finished():
        return _finished_game(response, gameid)
    else:
        return _running_game(response, gameid, None, api)

@APP.route('/game', methods=['GET', 'POST'])
def game_page():
    """
    View of the specified game, authentication-aware
    """
    # TODO: 3: every position should have a name
    # TODO: 2: chat
    gameid = request.args.get('gameid', None)
    if gameid is None:
        flash("Invalid game ID.")
        return redirect(url_for('error_page'))
    try:
        gameid = int(gameid)
    except ValueError:
        flash("Invalid game ID.")
        return redirect(url_for('error_page'))

    if is_authenticated():
        return authenticated_game_page(gameid)
    else:
        return unauthenticated_game_page(gameid)        

@APP.route('/analysis', methods=['GET'])
def analysis_page():
    """
    Analysis of a particular hand history item.
    """
    gameid = request.args.get('gameid', None)
    if gameid is None:
        return error("Invalid game ID.")
    try:
        gameid = int(gameid)
    except ValueError:
        return error("Invalid game ID (not a number).")

    api = API()
    response = api.get_public_game(gameid)
    if isinstance(response, APIError):
        if response is api.ERR_NO_SUCH_RUNNING_GAME:
            msg = "Invalid game ID."
        else:
            msg = "An unknown error occurred retrieving game %d, sorry." %  \
                (gameid,)
        return error(msg)
    game = response
    
    order = request.args.get('order', None)
    if order is None:
        return error("Invalid order.")
    try:
        order = int(order)
    except ValueError:
        return error("Invalid order (not a number).")

    try:
        item = game.history[order]
    except IndexError:
        return error("Invalid order (not in game).")

    if not isinstance(item, dtos.GameItemActionResult):
        return error("Invalid order (not a bet or raise).") 

    if not item.action_result.is_aggressive:
        return error("Analysis only for bets right now, sorry.")

    try:
        aife = game.analysis[order]
    except KeyError:
        return error("Analysis for this action is not ready yet.")

    street = game.game_details.situation.current_round
    street_text = aife.STREET_DESCRIPTIONS[street]
    if item.action_result.is_raise:
        action_text = "raises to %d" % (item.action_result.raise_total,)
    else:
        action_text = "bets %d" % (item.action_result.raise_total,)

    navbar_items = [('', url_for('home_page'), 'Home'),
                    ('', url_for('about_page'), 'About'),
                    ('', url_for('faq_page'), 'FAQ')]
    items_aggressive = [i for i in reversed(aife.items) if i.is_aggressive]
    items_passive = [i for i in aife.items if i.is_passive]
    items_fold = [i for i in aife.items if i.is_fold]
    # Could also have a status column or popover or similar:
    # "good bluff" = +EV
    # "bad bluff" = -EV on river
    # "possible semibluff" = -EV on river
    return render_template('web/analysis.html', gameid=gameid,
        street_text=street_text, screenname=item.user.screenname,
        action_text=action_text,
        items_aggressive=items_aggressive,
        items_passive=items_passive,
        items_fold=items_fold,
        is_raise=aife.is_raise, is_check=aife.is_check,
        navbar_items=navbar_items, is_logged_in=is_logged_in())
