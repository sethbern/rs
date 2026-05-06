# pyright: reportUndefinedVariable=false
# *******************************
# |docname| - route to a textbook
# *******************************
# This controller provides routes to admin functions
#
# Imports
# =======
# These are listed in the order prescribed by `PEP 8
# <http://www.python.org/dev/peps/pep-0008/#imports>`_.
#
# Standard library
# ----------------
import asyncio
import json
import logging
import os
import random
import re
import datetime
import requests

# Third Party
# -----------
import altair as alt
import pandas as pd
import redis
from dateutil.parser import parse
from rsptx.db.crud import fetch_api_token, fetch_lti_version
from rs_grading import _try_to_send_lti_grade  # pyright: ignore[reportMissingImports]

try:
    logger = logging.getLogger(settings.logger)
    logger.setLevel(settings.log_level)
except Exception:
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

peerjs = os.path.join("applications", request.application, "static", "js", "peer.js")
try:
    mtime = int(os.path.getmtime(peerjs))
except FileNotFoundError:
    mtime = random.randrange(10000)

request.peer_mtime = str(mtime)


@auth.requires(
    lambda: verifyInstructorStatus(auth.user.course_id, auth.user),
    requires_login=True,
)
def instructor():
    assignments = db(
        ((db.assignments.is_peer == True) | (db.assignments.kind == "Peer"))
        & (db.assignments.course == auth.user.course_id)
    ).select(orderby=~db.assignments.duedate)

    course_attrs = getCourseAttributesDict(auth.user.course_id)

    return dict(
        course_id=auth.user.course_name,
        course=get_course_row(db.courses.ALL),
        assignments=assignments,
        is_instructor=True,
        **course_attrs,
    )


# Instructor's interface to peer
# ------------------------------
@auth.requires(
    lambda: verifyInstructorStatus(auth.user.course_id, auth.user),
    requires_login=True,
)
def dashboard():
    """
    We track through questions by "submitting" the form that causes us
    to go to the next question.
    """
    assignment_id = request.vars.assignment_id
    if request.vars.next == "Next":
        next = True
    elif request.vars.next == "Reset":
        next = "Reset"
    else:
        next = False

    current_question, done, idx = _get_current_question(assignment_id, next)
    has_vote1 = _has_vote1(current_question.name, auth.user.username)
    all_questions = _get_assignment_questions(assignment_id)
    num_questions = len(all_questions)
    current_qnum = idx + 1

    assignment = db(db.assignments.id == assignment_id).select().first()
    course = db(db.courses.course_name == auth.user.course_name).select().first()

    course_attrs = getCourseAttributesDict(course.id, course.base_course)
    if "enable_ab" not in course_attrs:
        course_attrs["enable_ab"] = False

    if "latex_macros" not in course_attrs:
        course_attrs["latex_macros"] = ""
    db.useinfo.insert(
        course_id=auth.user.course_name,
        sid=auth.user.username,
        div_id=current_question.name,
        event="peer",
        act="start_question",
        timestamp=datetime.datetime.utcnow(),
    )

    is_lti = (
        asyncio.get_event_loop().run_until_complete(
            fetch_lti_version(auth.user.course_id)
        )
        != None
    )

    print("is_lti", is_lti)
    r = redis.from_url(os.environ.get("REDIS_URI", "redis://redis:6379/0"))
    r.hset(f"{auth.user.course_name}_state", "mess_count", "0")
    mess = {
        "sender": auth.user.username,
        "type": "control",
        "message": "enableNext",
        "broadcast": True,
        "course_name": auth.user.course_name,
    }
    r.publish("peermessages", json.dumps(mess))
    if "groupsize" not in course_attrs:
        course_attrs["groupsize"] = "3"

    return dict(
        course_id=auth.user.course_name,
        course=get_course_row(db.courses.ALL),
        current_question=current_question,
        all_questions=all_questions,
        current_qnum=current_qnum,
        num_questions=num_questions,
        assignment_id=assignment_id,
        assignment_name=assignment.name,
        is_instructor=True,
        is_last=done,
        lti=is_lti,
        has_vote1=has_vote1,
        peer_async_visible=assignment.peer_async_visible or False,
        **course_attrs,
    )


@auth.requires(
    lambda: verifyInstructorStatus(auth.user.course_id, auth.user),
    requires_login=True,
)
def toggle_async():
    response.headers["content-type"] = "application/json"
    assignment_id = request.vars.assignment_id
    if not assignment_id:
        return json.dumps({"ok": False, "error": "missing assignment_id"})
    assignment = db(db.assignments.id == assignment_id).select().first()
    if not assignment:
        return json.dumps({"ok": False, "error": "assignment not found"})
    course = db(db.courses.course_name == auth.user.course_name).select().first()
    if not course or assignment.course != course.id:
        return json.dumps({"ok": False, "error": "assignment does not belong to your course"})
    new_value = not (assignment.peer_async_visible or False)
    db(db.assignments.id == assignment_id).update(peer_async_visible=new_value)
    db.commit()
    return json.dumps({"peer_async_visible": new_value})


def extra():
    assignment_id = request.vars.assignment_id
    current_question, done, idx = _get_current_question(assignment_id, False)

    return dict(
        course_id=auth.user.course_name,
        course=get_course_row(db.courses.ALL),
        current_question=current_question,
        assignment_id=assignment_id,
        is_instructor=True,
    )


def _get_assignment_questions(assignment_id):
    all_questions = db(db.assignment_questions.assignment_id == assignment_id).select(
        orderby=[db.assignment_questions.sorting_priority, db.assignment_questions.id]
    )
    return [db.questions[question.question_id] for question in all_questions]


def _get_current_question(assignment_id, get_next):
    assignment = db(db.assignments.id == assignment_id).select().first()

    if get_next == "Reset":
        idx = 0
        db(db.assignments.id == assignment_id).update(current_index=idx)
    elif get_next is True:
        all_questions = _get_assignment_questions(assignment_id)
        total_questions = len(all_questions)
        idx = min(assignment.current_index + 1, max(total_questions - 1, 0))
        db(db.assignments.id == assignment_id).update(current_index=idx)
    else:
        idx = assignment.current_index
    db.commit()
    question, done = _get_numbered_question(assignment_id, idx)
    return question, done, idx


def _get_numbered_question(assignment_id, qnum):
    all_questions = _get_assignment_questions(assignment_id)
    total_questions = len(all_questions)

    if total_questions == 0:
        return None, "true"

    if qnum < 0:
        qnum = 0

    done = "false"
    if qnum >= total_questions:
        done = "true"
        qnum = total_questions - 1
    logger.debug(f"qnum = {qnum} total_questions = {total_questions} done = {done}")
    current_question = all_questions[qnum]
    return current_question, done


def _get_lastn_answers(num_answer, div_id, course_name, start_time, end_time=None):
    dburl = settings.database_uri.replace("postgres://", "postgresql://")
    if "?" in dburl:
        dburl = dburl[: dburl.index("?")]

    time_clause = f"""
        AND timestamp > '{start_time}'
        """
    if end_time:
        time_clause += f" AND timestamp < '{end_time}'"

    df = pd.read_sql_query(
        f"""
    WITH first_answer AS (
        SELECT
            *,
            ROW_NUMBER() OVER (
                PARTITION BY sid
                ORDER BY
                    id desc
            ) AS rn
        FROM
            mchoice_answers
        WHERE
            div_id = '{div_id}'
            AND course_name = '{course_name}'
            {time_clause}
    )
    SELECT
        *
    FROM
        first_answer
    WHERE
        rn <= {num_answer}
    ORDER BY
        sid
    limit 4000
    """,
        dburl,
    )
    df = df.dropna(subset=["answer"])
    logger.debug(df.head())
    # FIXME: this breaks for multiple answer mchoice!
    df = df[df.answer != ""]

    return df


def to_letter(astring: str):
    if astring.isnumeric():
        return chr(65 + int(astring))
    if "," in astring:
        alist = astring.split(",")
        alist = [chr(65 + int(x)) for x in alist]
        return ",".join(alist)
    return None


@auth.requires(
    lambda: verifyInstructorStatus(auth.user.course_id, auth.user),
    requires_login=True,
)
def chartdata():
    response.headers["content-type"] = "application/json"
    div_id = request.vars.div_id
    start_time = request.vars.start_time
    end_time = request.vars.start_time2  # start time of vote 2
    num_choices = request.vars.num_answers
    course_name = auth.user.course_name
    logger.debug(f"divid = {div_id}")
    df1 = _get_lastn_answers(1, div_id, course_name, start_time, end_time)
    if end_time:
        df2 = _get_lastn_answers(1, div_id, course_name, end_time)
        df2.rn = 2
        df = pd.concat([df1, df2])
    else:
        df = df1
    df["letter"] = df.answer.map(to_letter)
    x = df.groupby(["letter", "rn"])["answer"].count()
    df = x.reset_index()
    yheight = df.answer.max()
    alpha = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    y = pd.DataFrame(
        {
            "letter": list(alpha[:num_choices] * 2),
            "rn": [1] * num_choices + [2] * num_choices,
            "answer": [0] * num_choices * 2,
        }
    )
    df = df.merge(y, how="outer")
    c = (
        alt.Chart(df[df.rn == 1], title="First Answer")
        .mark_bar()
        .encode(
            x=alt.X("letter", axis=alt.Axis(title="Choice", labelAngle=0)),
            y=alt.Y(
                "sum(answer)",
                title="Number of Students",
                scale=alt.Scale(domain=(0, yheight)),
            ),
        )
    )
    d = (
        alt.Chart(df[df.rn == 2], title="Second Answer")
        .mark_bar()
        .encode(
            x=alt.X("letter", axis=alt.Axis(title="Choice", labelAngle=0)),
            y=alt.Y(
                "sum(answer)",
                title="Number of Students",
                scale=alt.Scale(domain=(0, yheight)),
            ),
        )
    )

    return alt.hconcat(c, d).to_json()


@auth.requires(
    lambda: verifyInstructorStatus(auth.user.course_id, auth.user),
    requires_login=True,
)
def num_answers():
    response.headers["content-type"] = "application/json"
    div_id = request.vars.div_id
    if not request.vars.start_time:
        return json.dumps({"count": 0, "mess_count": 0})

    acount = db(
        (db.mchoice_answers.div_id == div_id)
        & (db.mchoice_answers.course_name == auth.user.course_name)
        & (db.mchoice_answers.timestamp > parse(request.vars.start_time))
    ).count(distinct=db.mchoice_answers.sid)

    mess_count = db(
        (db.useinfo.div_id == div_id)
        & (db.useinfo.course_id == auth.user.course_name)
        & (db.useinfo.event == "sendmessage")
        & (db.useinfo.timestamp > parse(request.vars.start_time))
    ).count()

    return json.dumps({"count": acount, "mess_count": mess_count})


def percent_correct():
    div_id = request.vars.div_id
    start_time = request.vars.start_time
    course_name = request.vars.course_name
    df = _get_lastn_answers(1, div_id, course_name, start_time)
    logger.debug(f"Data Frame is {df}")
    tot = len(df)
    logger.debug(f"num rows = {tot}")
    corr = len(df[df.correct == "T"])
    if corr == 0:
        return json.dumps({"pct_correct": 0})
    else:
        return json.dumps({"pct_correct": corr / tot * 100})


#
# Student Facing pages
#
@auth.requires_login()
def student():
    if "access_token" not in request.cookies:
        # this means the user is logged in to web2py but not fastapi - this is not good
        # as the javascript in the questions assumes the new server and a token.
        logger.error(f"Missing Access Token: {auth.user.username} adding one Now")
        create_rs_token()

    assignments = db(
        ((db.assignments.is_peer == True) | (db.assignments.kind == "Peer"))
        & (db.assignments.course == auth.user.course_id)
        & (db.assignments.visible == True)
    ).select(orderby=~db.assignments.duedate)
    course = db(db.courses.course_name == auth.user.course_name).select().first()
    course_attrs = getCourseAttributesDict(course.id, course.base_course)
    if "latext_macros" not in course_attrs:
        course_attrs["latex_macros"] = ""

    return dict(
        course_id=auth.user.course_name,
        course=get_course_row(db.courses.ALL),
        assignments=assignments,
        **course_attrs,
    )


# Student's Interface to Peer Instruction
# ---------------------------------------
@auth.requires_login()
def peer_question():
    if "access_token" not in request.cookies:
        logger.warning(f"Missing Access Token: {auth.user.username} adding one now")
        create_rs_token()

    assignment_id = request.vars.assignment_id

    current_question, done, idx = _get_current_question(assignment_id, False)
    assignment = db(db.assignments.id == assignment_id).select().first()
    course = db(db.courses.course_name == auth.user.course_name).select().first()
    course_attrs = getCourseAttributesDict(course.id, course.base_course)
    if "latex_macros" not in course_attrs:
        course_attrs["latex_macros"] = ""

    return dict(
        course_id=auth.user.course_name,
        course=get_course_row(db.courses.ALL),
        current_question=current_question,
        assignment_name=assignment.name,
        assignment_id=assignment_id,
        **course_attrs,
    )


def find_good_partner(group, peeps, answer_dict):
    # try to find a partner with a different answer than the first group member
    logger.debug(f"here {group}, {peeps}, {answer_dict}")
    # the student did not answer this question
    if group[0] not in answer_dict:
        logger.error(f"student {group[0]} did not answer")
        return peeps.pop()
    ans = answer_dict[group[0]]
    i = 0
    while i < len(peeps) and answer_dict[peeps[i]] == ans:
        logger.debug(f"{i} : {peeps[i]}")
        i += 1

    if i < len(peeps):
        return peeps.pop(i)
    else:
        return peeps.pop()


def process_peep(p, peeps, target_list, other_list, in_person_groups, mode):
    target_list.append(p)
    peeps.remove(p)
    other_peeps = find_set_containing_string(in_person_groups, p)
    logger.debug(f"other_peeps = {other_peeps}")
    # if no other peeps then this person must be put into a chat group not an in-person group
    if not other_peeps and mode == "in_person":
        other_list.append(p)
        return
    for op in other_peeps:
        if op in peeps:
            peeps.remove(op)
            logger.debug(f"removed {op} from the peeps list")
        if op not in target_list:
            target_list.append(op)


@auth.requires(
    lambda: verifyInstructorStatus(auth.user.course_id, auth.user),
    requires_login=True,
)
def make_pairs():
    response.headers["content-type"] = "application/json"
    is_ab = request.vars.get("is_ab", False)
    div_id = request.vars.div_id
    df = _get_lastn_answers(1, div_id, auth.user.course_name, request.vars.start_time)
    group_size = int(request.vars.get("group_size", 2))
    r = redis.from_url(os.environ.get("REDIS_URI", "redis://redis:6379/0"))
    logger.debug(f"Clearing partnerdb_{auth.user.course_name}")
    r.delete(f"partnerdb_{auth.user.course_name}")

    logger.debug(f"STARTING to make pairs for {auth.user.course_name}")
    done = False
    # all peeps who answered peeps who didn't answer are filtered in
    # _get_lastn_answers
    peeps = df.sid.to_list()
    sid_ans = df.set_index("sid")["answer"].to_dict()

    # If the instructor is in the list of students, remove them
    if auth.user.username in peeps:
        peeps.remove(auth.user.username)

    # Shuffle the list of students
    random.shuffle(peeps)

    # Create a list of groups
    group_list = []
    done = len(peeps) == 0
    in_person_groups = []
    if is_ab:
        in_person_groups = _get_local_groups(auth.user.course_name)
        peeps_in_person = []
        peeps_in_chat = []

        peep_queue = [p for p in peeps if p in sid_ans]
        while peep_queue:
            p = peep_queue.pop()
            if p in peeps_in_person or p in peeps_in_chat:
                continue

            if random.random() < 0.5:
                logger.debug(f"adding {p} to the in_person list")
                process_peep(
                    p,
                    peeps,
                    peeps_in_person,
                    peeps_in_chat,
                    in_person_groups,
                    "in_person",
                )
            else:
                process_peep(
                    p, peeps, peeps_in_chat, peeps_in_person, in_person_groups, "chat"
                )
        # need to ensure that these peeps have answered the question
        peeps = [p for p in peeps_in_chat if p in sid_ans]
        # Now peeps contains only those who need to be paired up for chat
        logger.debug(f"FINAL PEEPS IN CHAT = {peeps}")
        logger.debug(f"FINAL PEEPS IN PERSON = {peeps_in_person}")
    done = len(peeps) == 0
    while not done:
        # Start a new group with one student
        group = [peeps.pop()]

        # Try to add more students to the group
        for i in range(group_size - 1):
            try:
                # Find a student with a different answer than the first student in the group
                group.append(find_good_partner(group, peeps, sid_ans))
            except IndexError:
                # If no more students are left to add, stop
                done = True
        # If the group only has one student, add them to the previous group
        if len(group) == 1:
            group_list[-1].append(group[0])
        else:
            # Otherwise add the group to the list of groups
            group_list.append(group)

        # Stop if all students have been grouped
        if len(peeps) == 0:
            done = True

    # Create a dictionary mapping each student to their group
    gdict = {}
    for group in group_list:
        for p in group:
            gl = group.copy()
            gl.remove(p)
            gdict[p] = gl

    # Save the groups to the redis database
    for k, v in gdict.items():
        r.hset(f"partnerdb_{auth.user.course_name}", k, json.dumps(v))
    r.hset(f"{auth.user.course_name}_state", "mess_count", "0")
    logger.info(f"DONE makeing pairs for {auth.user.course_name} {gdict}")
    # todo: if we are doing AB testing then we need not broadcast or maybe broadcast,
    # but with a way for individual students to know if they are in person or not
    # maybe a in_persondb paralell to the partnerdb that can be sent like the enableChat
    # which is not broadcast!
    _broadcast_peer_answers(sid_ans)
    logger.info(f"DONE broadcasting pair information")
    if is_ab:
        _broadcast_faceChat(peeps_in_person, in_person_groups)
    return json.dumps("success")


def find_set_containing_string(list_of_sets, target_string):
    # iterating over all sets ensures that even if someone forgets to enter their group
    # or someone accidentally leaves someone out of the group we will still find them
    result_set = set()
    for s in list_of_sets:
        if target_string in s:
            result_set |= s
    return result_set


def _get_local_groups(course_name):
    query = f"""
SELECT u1.*
FROM useinfo u1
JOIN (
    SELECT sid, MAX(timestamp) AS last_entry
    FROM useinfo
    WHERE course_id = '{course_name}' and event = 'peergroup'
    GROUP BY sid
) u2 ON u1.sid = u2.sid AND u1.timestamp = u2.last_entry
WHERE u1.course_id = '{course_name}' and u1.event = 'peergroup';
"""
    in_person_groups = []
    res = db.executesql(query)
    for row in res:
        logger.debug(row)
        # act is index 4
        peeps = row[4].split(":")[1]
        peeps = set(peeps.split(","))
        # sid is index 2
        if row[2] not in peeps:
            peeps.add(row[2])
        in_person_groups.append(peeps)

    return in_person_groups


def _broadcast_peer_answers(answers):
    """
    The correct and incorrect lists are dataframes that containe the sid and their answer
    We want to iterate over the
    """

    r = redis.from_url(os.environ.get("REDIS_URI", "redis://redis:6379/0"))
    for p1, p2 in r.hgetall(f"partnerdb_{auth.user.course_name}").items():
        p1 = p1.decode("utf8")
        partner_list = json.loads(p2)
        pdict = {}
        for p2 in partner_list:
            ans = to_letter(answers.get(p2, None))
            pdict[p2] = ans
        # create a message from p1 to put into the publisher queue
        # it seems odd to not have a to field in the message...
        # but it is not necessary as the client can figure out how it is to
        # based on who it is from.
        mess = {
            "type": "control",
            "from": p1,
            "to": p1,
            "message": "enableChat",
            "broadcast": False,
            "answer": json.dumps(pdict),
            "course_name": auth.user.course_name,
        }
        r.publish("peermessages", json.dumps(mess))


def _broadcast_faceChat(peeps, in_person_groups):
    """
    Send the message to enable the face chat to the students in the peeps list
    """

    people = db(db.auth_user.course_name == auth.user.course_name).select(
        db.auth_user.username, db.auth_user.first_name, db.auth_user.last_name
    )
    # create a dictionary of people with their usernames as keys
    peeps_dict = {p.username: f"{p.first_name} {p.last_name}" for p in people}

    r = redis.from_url(os.environ.get("REDIS_URI", "redis://redis:6379/0"))
    for p in peeps:
        # create a message from p1 to put into the publisher queue
        # it seems odd to not have a to field in the message...
        # but it is not necessary as the client can figure out how it is to
        # based on who it is from.
        # todo use _get_local_groups to get the in person groups
        pgroup = set()
        for group in in_person_groups:
            if p in group:
                pgroup = group
                break
        pgroup = [peeps_dict.get(x, x) for x in pgroup]  # convert usernames to names
        mess = {
            "type": "control",
            "from": p,
            "to": p,
            "message": "enableFaceChat",
            "broadcast": False,
            "group": pgroup,
            "course_name": auth.user.course_name,
        }
        r.publish("peermessages", json.dumps(mess))


def clear_pairs():
    response.headers["content-type"] = "application/json"
    r = redis.from_url(os.environ.get("REDIS_URI", "redis://redis:6379/0"))
    r.delete(f"partnerdb_{auth.user.course_name}")
    return json.dumps("success")


def publish_message():
    response.headers["content-type"] = "application/json"
    r = redis.from_url(os.environ.get("REDIS_URI", "redis://redis:6379/0"))
    data = json.dumps(request.vars)
    logger.info(
        f"PEERCOM data = {data} {os.environ.get('REDIS_URI', 'redis://redis:6379/0')}"
    )
    r.publish("peermessages", data)
    res = r.hget(f"{auth.user.course_name}_state", "mess_count")
    if res is not None:
        mess_count = int(res)
    else:
        mess_count = 0
    if request.vars.type == "text":
        r.hset(f"{auth.user.course_name}_state", "mess_count", str(mess_count + 1))
    return json.dumps("success")


def log_peer_rating():
    response.headers["content-type"] = "application/json"
    current_question = request.vars.div_id
    peer_sid = request.vars.peer_id
    r = redis.from_url(os.environ.get("REDIS_URI", "redis://redis:6379/0"))
    retmess = "Error: no peer to rate"
    if peer_sid:
        db.useinfo.insert(
            course_id=auth.user.course_name,
            sid=auth.user.username,
            div_id=current_question,
            event="ratepeer",
            act=f"{peer_sid}:{request.vars.rating}",
            timestamp=datetime.datetime.utcnow(),
        )
        retmess = "success"

    return json.dumps(retmess)


@auth.requires_login()
def peer_async():
    if "access_token" not in request.cookies:
        logger.warning(f"Missing Access Token: {auth.user.username} adding one now")
        create_rs_token()

    assignment_id = request.vars.assignment_id

    question_num = 1
    if request.vars.question_num is not None:
        question_num = int(request.vars.question_num)

    current_question, all_done = _get_numbered_question(assignment_id, question_num - 1)
    total_questions = len(_get_assignment_questions(assignment_id))

    assignment = db(db.assignments.id == assignment_id).select().first()

    has_vote1 = False
    has_reflection = False

    if current_question:
        div_id = current_question.name
        sid = auth.user.username
        has_vote1 = _has_vote1(div_id, sid)
        has_reflection = _has_reflection(div_id, sid)

    course = db(db.courses.course_name == auth.user.course_name).select().first()
    course_attrs = getCourseAttributesDict(course.id, course.base_course)

    if "latex_macros" not in course_attrs:
        course_attrs["latex_macros"] = ""

    aq = None
    if current_question:
        aq = db(
            (db.assignment_questions.assignment_id == assignment_id)
            & (db.assignment_questions.question_id == current_question.id)
        ).select().first()
    async_llm_modes_enabled = course_attrs.get("enable_async_llm_modes", "false") == "true"
    if async_llm_modes_enabled:
        question_async_mode = (aq.async_mode or "standard") if aq else "standard"
        llm_enabled = _llm_enabled() and question_async_mode in ("llm", "analogies")
    else:
        llm_enabled = _llm_enabled()
    try:
        db.useinfo.insert(
            course_id=auth.user.course_name,
            sid=auth.user.username,
            div_id=current_question.name if current_question else None,
            event="pi_mode",
            act=json.dumps({"mode": "llm" if llm_enabled else "legacy"}),
            timestamp=datetime.datetime.utcnow(),
        )
    except Exception:
        logger.exception("Failed to log pi_mode for peer_async")

    from pi_themes import THEMES as PI_THEMES

    return dict(
        course_id=auth.user.course_name,
        course=get_course_row(db.courses.ALL),
        current_question=current_question,
        assignment_id=assignment_id,
        assignment_name=assignment.name,
        nextQnum=question_num + 1,
        total_questions=total_questions,
        is_last_question=(question_num >= total_questions),
        all_done=all_done,
        has_vote1=has_vote1,
        has_reflection=has_reflection,
        llm_enabled=llm_enabled,
        async_mode=question_async_mode if async_llm_modes_enabled else "standard",
        pi_themes_json=json.dumps(PI_THEMES),
        llm_reply=None,
        **course_attrs,
    )


@auth.requires_login()
def get_async_explainer():
    course_name = request.vars.course
    div_id = request.vars.div_id

    messages = db(
        (db.useinfo.event.belongs(["sendmessage", "reflection"]))
        & (db.useinfo.div_id == div_id)
        & (db.useinfo.course_id == course_name)
    ).select(orderby=db.useinfo.id)

    all_msgs = []  #list of (sid, msg) in insertion order
    last_per_sid = {}
    for row in messages:
        if row.event == "reflection":
            msg = row.act
        else:
            try:
                msg = row.act.split(":", 2)[2]
            except Exception:
                msg = row.act
        if last_per_sid.get(row.sid) != msg:  #skip exact consecutive duplicates only
            all_msgs.append((row.sid, msg))
            last_per_sid[row.sid] = msg

    llm_turns = db(
        (db.useinfo.event == "pi_llm_turn")
        & (db.useinfo.div_id == div_id)
        & (db.useinfo.course_id == course_name)
    ).select(orderby=db.useinfo.id)

    llm_by_sid = {}
    for row in llm_turns:
        try:
            turn = json.loads(row.act)
            attempt_id = turn.get("pi_attempt_id", "")
            turn_index = turn.get("turn_index", 0)
            role = turn.get("role", "")
            content = turn.get("content", "")
            if row.sid not in llm_by_sid:
                llm_by_sid[row.sid] = {}
            if attempt_id not in llm_by_sid[row.sid]:
                llm_by_sid[row.sid][attempt_id] = []
            llm_by_sid[row.sid][attempt_id].append((turn_index, role, content))
        except Exception:
            pass

    parts = []
    sids_with_llm_shown = set()
    for sid, msg in all_msgs:
        parts.append(f"<li><strong>{sid}</strong> said: {msg}</li>")
        if sid in llm_by_sid and sid not in sids_with_llm_shown:
            sids_with_llm_shown.add(sid)
            latest_attempt = max(
                llm_by_sid[sid].keys(),
                key=lambda a: max(t[0] for t in llm_by_sid[sid][a])
            )
            turns = sorted(llm_by_sid[sid][latest_attempt], key=lambda t: t[0])
            for _, role, content in turns:
                if role == "assistant":
                    parts.append(f"<li><strong>LLM Peer</strong> said: {content}</li>")

    for sid, attempts in llm_by_sid.items():
        if sid not in sids_with_llm_shown and not any(s == sid for s, _ in all_msgs):
            latest_attempt = max(attempts.keys(), key=lambda a: max(t[0] for t in attempts[a]))
            turns = sorted(attempts[latest_attempt], key=lambda t: t[0])
            for _, role, content in turns:
                if role == "assistant":
                    parts.append(f"<li><strong>LLM Peer</strong> said: {content}</li>")

    if not parts:
        mess = "Sorry there are no explanations yet."
    else:
        mess = "<ul>" + "".join(parts) + "</ul>"

    logger.debug(f"Get message for {div_id}")
    return json.dumps({"mess": mess, "user": "", "answer": "", "responses": {}})


def _get_mcq_context(div_id):
    import re
    q = db(db.questions.name == div_id).select().first()
    if not q:
        logger.error(f"_get_mcq_context: no question row for {div_id}")
        return "", "", []

    question = (q.question or "").strip()

    if not question and hasattr(q, "htmlsrc") and q.htmlsrc:
        html = q.htmlsrc
        p_tags = re.findall(r'<p>(.*?)</p>', html, re.DOTALL)
        if p_tags:

            raw_parts = []
            for p in p_tags:
                clean = re.sub(r'<[^>]+>', '', p).strip()
                if clean:
                    raw_parts.append(clean)
            question_parts = []
            for part in raw_parts:
                if part.startswith('/') and len(part) < 30:
                    break
                question_parts.append(part)
            question = "\n".join(question_parts)

        answer_matches = re.findall(r'data-component="answer"[^>]*>.*?<p>(.*?)</p>', html, re.DOTALL)
        if answer_matches:
            choices = []
            for i, ans in enumerate(answer_matches):
                clean = re.sub(r'<[^>]+>', '', ans).strip()
                choices.append(f"{chr(65+i)}. {clean}")
            code = ""
            if hasattr(q, "code") and q.code:
                code = q.code.strip()
            return question, code, choices

    code = ""
    if hasattr(q, "code") and q.code:
        code = q.code.strip()
    choices = []
    try:
        if hasattr(q, "answers") and q.answers:
            opts = json.loads(q.answers)
            for i, opt in enumerate(opts):
                choices.append(f"{chr(65+i)}. {opt.strip()}")
    except Exception as e:
        logger.warning(f"Could not parse choices for {div_id}: {e}")
    return question, code, choices


# handle async peer instruction reflection using an LLM:
# logs student messages and return an LLM peer-style reply
@auth.requires_login()
def get_async_llm_reflection():
    logger.warning("LLM REFLECTION CALLED")
    logger.warning(f"raw body = {request.body.read()}")
    request.body.seek(0)
    try:
        data = json.loads(request.body.read().decode("utf-8"))
    except Exception:
        return response.json(dict(ok=False, error="invalid json"))

    div_id = (data.get("div_id") or "").strip()
    selected = (data.get("selected_answer") or "").strip()
    messages = data.get("messages")
    theme_id = (data.get("theme_id") or "").strip()
    try:
        sid = auth.user.username
        course_name = auth.user.course_name

        user_msgs = [m for m in messages if m.get("role") == "user"]

        for idx, m in enumerate(user_msgs):
            content = (m.get("content") or "").strip()
            if not content:
                continue

            if idx == 0:
                db.useinfo.insert(
                    course_id=course_name,
                    sid=sid,
                    div_id=div_id,
                    event="reflection",
                    act=content,
                    timestamp=datetime.datetime.utcnow(),
                )
            else:
                db.useinfo.insert(
                    course_id=course_name,
                    sid=sid,
                    div_id=div_id,
                    event="sendmessage",
                    act=f"to:llm:{content}",
                    timestamp=datetime.datetime.utcnow(),
                )
    except Exception as e:
        logger.exception("Failed to log LLM user message")

    if not div_id:
        return response.json(dict(ok=False, error="missing div_id"))

    question, code, choices = _get_mcq_context(div_id)

    if not question and not choices:
        question = (data.get("question_text") or "").strip()

    analogy_mapping = (data.get("analogy_mapping") or "").strip()
    generated_mapping = ""
    generated_first_message = ""

    reflection_text = (data.get("reflection") or "").strip()
    if not reflection_text and messages:
        user_msgs = [m for m in messages if m.get("role") == "user"]
        if user_msgs:
            reflection_text = (user_msgs[0].get("content") or "").strip()

    if theme_id and not analogy_mapping:
        from pi_themes import THEME_BY_ID
        theme_obj = THEME_BY_ID.get(theme_id)
        if theme_obj:
            generated_mapping, generated_first_message = _generate_analogy_mapping(
                question, code, choices, theme_obj["label"], selected=selected, reflection=reflection_text
            )
            if generated_mapping:
                analogy_mapping = generated_mapping

    analogy_preamble = ""
    if theme_id and analogy_mapping:
        from pi_themes import THEME_BY_ID
        theme = THEME_BY_ID.get(theme_id)
        if theme:
            theme_label = theme["label"]
            analogy_preamble = (
                f"the student chose '{theme_label}' as their analogy theme. here is a structural mapping between the question and the theme:\n"
                f"{analogy_mapping}\n"
                f"\n"
                f"use this mapping to frame your conversation naturally. the student has never seen this mapping — you are introducing this scenario to them for the first time.\n"
                f"IMPORTANT: in conversation only ever use the RIGHT side of the mapping (the theme terms). never use the LEFT side (the CS/file system terms) when talking in the analogy — so if the mapping says 'project/ folder -> dairy section' you say 'dairy section' never 'project folder' or 'project aisle'. this means never say words like 'staging', 'commit', 'repository', 'directory', 'git', or any CS term while you are in the analogy — stay fully in theme vocabulary until the explicit bridge-back moment.\n"
                f"in your first message: paint the scenario in natural language — describe the situation as if you are telling a story, not reading a list. do not recite the mapping labels (e.g. do not say 'the move action' or 'forest of wisdom' as if they are technical terms — say 'imagine you're walking through...' or 'so you're in the...'). place the student in the scene concretely, then connect it to what they said, then ask a question. do not say 'our scenario' — introduce it fresh.\n"
                f"in follow-ups: keep using the theme vocabulary. if the student engages with it, build on it. when they seem to understand the structure through the theme, bridge back to the actual question.\n"
                f"do not formally announce the analogy. do not say 'in the {theme_label} analogy' or 'using {theme_label} as a metaphor'. just talk in those terms naturally.\n"
                f"if the student makes a claim or assumption, use the theme to have them test it — like 'hmm would that floor even exist in that building tho?'\n"
            )

    base_rules = (
        "only speak in lower case.\n"
        "you are a student talking to another student during peer instruction.\n"
        "you are both looking at the same multiple choice question with code and answers.\n"
        "you remember the question and choices.\n"
        "most messages should be short (1 to 3 sentences often very short).\n"
        "use casual informal language and common typos.\n"
        "never use commas.\n"
        "never use gendered language.\n"
        "do not use new lines.\n"
        "never use em dashes (—) or formal punctuation. write the way you actually talk.\n"
        "do not sound like a teacher.\n"
        "do not explain step by step.\n"
        "never say something is right or wrong.\n"
        "STRICT RULE: every message must begin with one of these allowed openers: a question word ('what', 'where', 'how', 'do', 'can', 'if', 'wait', 'hmm'), or the word 'so' followed immediately by a scenario observation (not 'so exactly' or 'so right'). never begin with 'yeah', 'right', 'exactly', 'correct', 'yes', 'yep', 'true', 'good', 'nice', 'great', 'perfect', 'cool', 'totally', 'sure', 'got it', or any variation. these imply the student is correct. check your first word before every message.\n"
        "never confirm or explain the code outcome after the student says something — only ask them to keep tracing or elaborate.\n"
        "if the student introduces a new assumption or claim, do not build on it as if it is correct — instead use the analogy to make them test that assumption themselves. for example if they claim 'it creates the directory' ask them through the analogy: 'does trying to walk to a floor that doesn't exist create it, or does something else happen?' never validate the assumption, never deny it — just ask them to examine it through the scenario.\n"
        "if an analogy scenario was established, always bring follow-up questions back through that scenario — do not abandon it for direct code talk. never use CS terms (like 'staging', 'commit', 'directory', 'repository', 'file path') in a message that is otherwise in analogy vocabulary — stay fully in the theme language until you are explicitly bridging back.\n"
        "each follow-up must move the conversation forward — do not ask the same question twice in different words. if the student answered your last question, accept that and go one level deeper or bridge back to the actual problem.\n"
        "when referencing a structural detail of the scenario in a follow-up, briefly restate that detail in the same message — do not assume the student remembers the exact structure from the first message. for example: 'remember in that tree the grandparent has two children — user and shared. you are currently under user...' then ask your question.\n"
        "never say things like 'let's focus on the code' or 'going back to the code' — always route through the scenario instead.\n"
        "if the student themselves references the analogy, use that as an opening to deepen it — never redirect away from it.\n"
        "once the student has traced through the analogy and seems to understand the structure, explicitly bridge back to the question — ask them to apply that same reasoning to the actual problem values or code.\n"
        "do not let the analogy float indefinitely without connecting it back to the question. the goal is for the student to say 'oh so in the question that means...' — guide them there.\n"
        "never use phrases like 'not quite' or 'not exactly' or 'almost' or 'close' or 'not yet' or any phrase that implies the student is incorrect.\n"
        "never react to whether the student's answer is correct or incorrect — only ask them to explain their reasoning.\n"
        "never use the analogy to imply the student's answer is wrong.\n"
        "never end a message with a rhetorical question whose obvious answer signals the student is wrong — like 'does that room just appear?' or 'is there really a backup area there?' — these tell the student they are wrong.\n"
        "instead: have the student trace through the scenario step by step. ask where they end up after each step, or ask them to walk you through what they think each part of the command does in the scenario. keep questions open — 'where does that put you?' not 'does that even exist?'\n"
        "the student should discover whether their answer is right or wrong by tracing through the analogy themselves.\n"
        "never connect the analogy conclusion back to a specific answer choice — do not say things like 'answer B says you need to be in X — does that match up with needing to be in the new server?' because this confirms the answer without saying it. if the student has traced through the analogy and reached a conclusion ask them what that tells them about the problem and let THEM connect it back to their choice — never make that connection for them.\n"
        "do not pretend to have picked an answer yourself.\n"
        "never mention a choice letter as the correct answer.\n"
        "if the question includes code never clearly describe the final result or fully state what it prints.\n"
        "if the question does not include code do not make up or reference code that is not there.\n"
        "only refer to what is actually in the question.\n"
        "be aware of common misconceptions but do not introduce them yourself.\n"
        "if there is code refer to it loosely like 'that line' or 'the loop' or 'the print'.\n"
        "often hedge with uncertainty.\n"
        "ask the other student to explain why they picked their answer and how they reasoned through it.\n"
        "ask follow up questions about their reasoning like 'what makes you think that' or 'how did you trace through it'.\n"
        "do not push them toward a different answer or imply their answer is wrong.\n"
        "never reveal or hint at which answer is correct or incorrect.\n"
        "never say things like 'the feedback says' or 'according to the answer' or reference any grading or correctness information.\n"
        "do not make up information that is not in the question.\n"
        "if you are unsure about something say so honestly instead of guessing.\n"
        "if the other student mentions the same answer more than once or sounds confident in their answer you must tell them to go ahead and vote again — this overrides everything else.\n"
        "do not ask another question or continue the analogy after they have confirmed their answer — just tell them to vote.\n"
        "do not continue reasoning after telling them to vote again.\n"
        "focus on getting them to think through the problem not on changing their mind.\n\n"
    )

    context_suffix = ""
    if question:
        context_suffix += f"question:\n{question}\n\n"
    if code:
        context_suffix += f"code:\n{code}\n\n"
    if choices:
        context_suffix += "answer choices:\n" + "\n".join(choices) + "\n\n"
    if selected:
        context_suffix += f"the other student chose: {selected}\n\n"

    def build_system_msg(preamble):
        return {"role": "system", "content": preamble + base_rules + context_suffix}

    system_msg = build_system_msg(analogy_preamble)

    if not messages:
        reflection = (data.get("reflection") or "").strip()
        if not reflection:
            return response.json(dict(ok=False, error="missing reflection"))

        messages = [
            system_msg,
            {
                "role": "user",
                "content": (
                    f"i chose answer {selected}. "
                    f"my explanation was:\n\n{reflection}"
                ),
            },
        ]

    else:
        if not isinstance(messages, list):
            return response.json(dict(ok=False, error="messages must be a list"))

        if len(messages) == 0 or messages[0].get("role") != "system":
            messages = [system_msg] + messages
        else:
            messages[0] = system_msg

    try:
        if generated_first_message and len([m for m in messages if m.get("role") == "user"]) <= 1:
            reply = generated_first_message
        else:
            reply = _call_openai(messages)
        try:
            db.useinfo.insert(
                course_id=auth.user.course_name,
                sid=auth.user.username,
                div_id=div_id,
                event="llm_peer_sendmessage",
                act=f"to: student:{reply}",
                timestamp=datetime.datetime.utcnow(),
            )
        except Exception:
            logger.exception("Failed to log LLM reply")

        if not reply:
            return response.json(
                dict(ok=False, error="llm returned empty reply (missing api key?)")
            )
        result = dict(ok=True, reply=reply)
        if generated_mapping:
            result["analogy_mapping"] = generated_mapping
        return response.json(result)
    except Exception as e:
        logger.exception("LLM reflection failed")
        return response.json(dict(ok=False, error=str(e)))


def _get_user_answer(div_id, s):
    ans = (
        db(
            (db.useinfo.event == "mChoice")
            & (db.useinfo.sid == s)
            & (db.useinfo.div_id == div_id)
            & (db.useinfo.act.like("%vote1"))
        )
        .select(orderby=~db.useinfo.id)
        .first()
    )
    if ans:
        return ans.act.split(":")[1]
    else:
        return ""


# check if the student has already submitted a reflection for the question
def _has_reflection(div_id, sid):
    row = (
        db(
            (db.useinfo.event == "reflection")
            & (db.useinfo.sid == sid)
            & (db.useinfo.div_id == div_id)
        )
        .select(orderby=~db.useinfo.id)
        .first()
    )
    return row is not None


def _has_vote1(div_id, sid):
    row = (
        db(
            (db.useinfo.event == "mChoice")
            & (db.useinfo.sid == sid)
            & (db.useinfo.div_id == div_id)
            & (db.useinfo.act.like("%vote1"))
        )
        .select(orderby=~db.useinfo.id)
        .first()
    )
    return row is not None


def _get_user_messages(user, div_id, course_name):
    # this gets both sides of the conversation -- thus the | in the query below.
    messages = db(
        (db.useinfo.event == "sendmessage")
        & ((db.useinfo.sid == user) | (db.useinfo.act.like(f"to:{user}%")))
        & (db.useinfo.div_id == div_id)
        & (db.useinfo.course_id == course_name)
    ).select(orderby=db.useinfo.id)
    user = messages[0].sid
    mess = "<ul>"
    participants = set()
    for row in messages:
        mpart = row.act.split(":")[2]
        mess += f"<li>{row.sid} said: {mpart}</li>"
        participants.add(row.sid)
    mess += "</ul>"

    return mess, participants


@auth.requires(
    lambda: verifyInstructorStatus(auth.user.course_id, auth.user),
    requires_login=True,
)
def send_lti_scores():
    response.headers["content-type"] = "application/json"
    assignment_id = request.vars.assignment_id
    grades = db(db.grades.assignment == assignment_id).select()
    for sid in grades:
        _try_to_send_lti_grade(sid, assignment_id, force=True)

    return json.dumps("success")


# determine whether LLM-based async peer discussion is enabled for this course based on coursewide api key
def _llm_enabled():
    return bool(_get_course_openai_key())

#fetch the course-wide openai API key used to enable LLM-based async peer discussion (only works for openai currently)
def _get_course_openai_key():
    try:
        course = db(db.courses.course_name == auth.user.course_name).select().first()

        if not course:
            logger.warning("PEER LLM: no course row found for %s", auth.user.course_name)
            return ""

        logger.warning("PEER LLM: looking up token for course_id=%s (%s)",
                        course.id, auth.user.course_name)

        rows = db.executesql(
            "SELECT token FROM api_tokens "
            "WHERE course_id = %s AND provider = %s "
            "ORDER BY last_used ASC NULLS FIRST LIMIT 1",
            placeholders=[course.id, "openai"],
        )
        logger.warning("PEER LLM: executesql returned %d rows", len(rows) if rows else 0)

        if rows and rows[0][0]:
            from cryptography.fernet import Fernet
            secret = os.environ.get("FERNET_SECRET", "").strip()
            if not secret:
                raise RuntimeError("FERNET_SECRET environment variable is not set")
            f = Fernet(secret.encode() if isinstance(secret, str) else secret)
            encrypted = rows[0][0]
            decrypted = f.decrypt(encrypted.encode()).decode().strip()
            logger.warning("PEER LLM: decrypted key for course %s: %s****",
                            course.id, decrypted[:4])
            return decrypted

        logger.warning("PEER LLM: no openai token found for course_id=%s", course.id)
    except Exception:
        logger.exception("Failed to fetch course-wide OpenAI token for peer LLM")

    return ""


# Focused LLM call to produce a structural mapping for the analogy mode.
# Returns a mapping string that the conversation model uses to frame its messages.
def _generate_analogy_mapping(question, code, choices, theme_label, selected="", reflection=""):
    """
    Produces a structural mapping between the question's CS concept and the
    student's chosen theme, plus the first message to send to the student.

    Returns (mapping_string, first_message) or ("", "") on failure.
    """
    context_parts = []
    if question:
        context_parts.append(f"Question: {question}")
    if code:
        context_parts.append(f"Code:\n{code}")
    if choices:
        context_parts.append("Choices:\n" + "\n".join(choices))
    if selected:
        context_parts.append(f"Student's answer: {selected}")
    if reflection:
        context_parts.append(f"Student's explanation: {reflection}")
    context = "\n\n".join(context_parts)

    prompt = (
        f"Read this CS question carefully:\n\n"
        f"{context}\n\n"
        f"The student chose '{theme_label}' as their analogy theme.\n\n"
        f"Step 1: Identify the underlying CS concept this question is testing — one sentence, specific about what structurally happens (not just the topic name).\n\n"
        f"Step 2: Break the question's structure down into its meaningful spatial or logical elements — the starting point, the required location to perform the action, the target item, and the action itself. Do NOT include the command syntax itself as an element. Do NOT evaluate the outcome or describe dependencies (do not write elements like 'the outcome depends on...' or 'the action requires...'). Focus only on what factually exists: where the student currently is, where the target item is, and what location is needed to perform the action. Describe elements factually and neutrally. The structure should describe what exists, not what the right reasoning is. Use bullet points, one element per line.\n\n"
        f"Step 3: Find a real, concrete, familiar situation in '{theme_label}' that structurally mirrors the question. Map each element from step 2 to a specific, real, recognizable thing from that situation — not invented names or generic labels. CRITICAL: never use CS or file system terminology on the right side of the mapping — do not name a theme item after a folder name, variable name, or command (e.g. do not write 'project aisle' or 'backup section' — those are just CS names with a theme word appended). Instead use things that actually exist in '{theme_label}' (e.g. 'dairy section', 'frozen foods aisle', 'checkout counter'). The theme items should feel like something a person familiar with '{theme_label}' would immediately picture with no knowledge of CS. CRITICAL: if the question is about being in the right location to perform an action, the target item DOES exist at the required location — the only issue is whether the student is in the right place to reach it. Make this clear in the mapping: the item is there, the student just needs to get there. Do not create any ambiguity about whether the item exists. The required location must be somewhere the person would normally go — do not map it to a staff-only or restricted area (e.g. do not use 'back stockroom' in a grocery store — customers do not go there; use a specific aisle or section instead). The scene must start in a natural, already-stable state — do not invent events to explain how things came to be. IMPORTANT: if the question involves navigating toward a root or parent (e.g. `..` in a file path), that means moving toward the outermost container — in a building this is the ground floor or lobby, NOT a higher floor. Deep nested = higher up, closer to root = lower/ground. Make sure the direction in your theme matches this intuition. CRITICAL: the mapped action must be a concrete, physical, presence-required activity — something that only makes sense when you are physically at the required location. Do NOT map 'edit/run/modify a file' to any kind of writing, editing, or rewriting action on a document, card, or paper — documents are portable and can be edited anywhere, which breaks the analogy. Instead map the CS action to the act of REACHING, GRABBING, OR USING a fixed item that lives at that location: reach for a jar on a shelf, use the blender on the counter, pick up a bag at a carousel, order at a specific counter. The mapped item should be something fixed at the required location that you can only interact with by being there.\n\n"
        f"Step 4: Write the first message the LLM peer will send to the student. This message should:\n"
        f"- Be in casual, lowercase, peer voice — like a student talking to another student\n"
        f"- Use minimal commas\n"
        f"- Introduce the scene to the student from scratch — they have never heard of this scenario. Do not say 'i'm picturing X' or reference the scenario as if they already know it. Start with 'imagine you're in...' or 'so picture this...' to actually place them in the situation\n"
        f"- Set the scene in 1-2 sentences using theme vocabulary only — no file paths, no CS terms, no variable names\n"
        f"- Name the specific locations from your mapping (e.g. 'the dairy section' not 'an aisle') so the conversation is grounded from the start\n"
        f"- After placing them in the scene, ask a specific, concrete location question using the exact mapped action — e.g. 'can you board your flight from the lobby or do you need to get to gate 12 first?' not vague questions like 'can you use it from here' or 'where does the item end up'. The action in the question must be the specific mapped action, not a generic 'use it' or 'access it'.\n"
        f"- Do NOT assume the student is wrong — the question works whether they are right or wrong\n"
        f"- Never imply the student is wrong or ask a rhetorical question with an obvious answer\n"
        f"- Never say 'our scenario' or announce it as an analogy\n"
        f"- Do not narrate the full trace — set the scene then ask them to trace it\n\n"
        f"Output ONLY in this exact format:\n"
        f"CONCEPT: [one sentence]\n"
        f"STRUCTURE:\n"
        f"- [element 1]\n"
        f"- [element 2]\n"
        f"- ...\n"
        f"MAPPING:\n"
        f"- [element 1] -> [theme equivalent]\n"
        f"- [element 2] -> [theme equivalent]\n"
        f"- ...\n"
        f"FIRST_MESSAGE: [the first message to send]\n"
    )

    try:
        raw = _call_openai([{"role": "user", "content": prompt}], model_override="gpt-5.4-mini")
        raw = raw.strip()
        first_message = ""
        mapping = raw
        if "FIRST_MESSAGE:" in raw:
            parts = raw.split("FIRST_MESSAGE:", 1)
            mapping = parts[0].strip()
            first_message = parts[1].strip()
        return mapping, first_message
    except Exception:
        logger.exception("Failed to generate analogy mapping")
        return "", ""


def _call_openai(messages, model_override=None):
    """
    Minimal HTTP call using the instructor-provided course-wide OpenAI token.
    messages: list of {role, content}
    model_override: optional model name (e.g. "gpt-4o") to use instead of the default
    returns reply string
    """
    api_key = _get_course_openai_key()
    if not api_key:
        raise Exception("missing api key")

    default_model = os.environ.get("PI_OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    model = model_override or default_model
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.4,
        "max_completion_tokens": 300,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    logger.warning(f"PEER LLM CALL | provider=openai-course-token | model={model} | status={resp.status_code}")
    if not resp.ok:
        logger.error(f"PEER LLM ERROR | model={model} | status={resp.status_code} | body={resp.text[:500]}")
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()
