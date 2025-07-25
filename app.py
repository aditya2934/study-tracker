import streamlit as st
import firebase_admin
from firebase_admin import credentials, db
from datetime import date
import uuid
import pandas as pd
import json
import time
import streamlit.components.v1 as components # Import components

# --- CONFIGURATION (using Streamlit Secrets) ---
DATABASE_URL = st.secrets["firebase"]["database_url"]
DB_PATH = "tasks"

# --- AUDIO ASSETS (URLs) ---
# Using reliable free sound sources. Replace with your own if you prefer.
POMODORO_FINISH_SOUND_URL = "https://www.soundjay.com/buttons/sounds/button-16.mp3"
TASK_TICK_SOUND_URL = "https://www.soundjay.com/buttons/sounds/button-48.mp3"
WHITE_NOISE_URL = "https://www.soundjay.com/nature/sounds/whitenoise-1.mp3" # Looping white noise


# --- FIREBASE INITIALIZATION ---
def initialize_firebase():
    if not firebase_admin._apps:
        try:
            # Attempt to parse private_key correctly if it contains newlines
            firebase_private_key = st.secrets["firebase"]["private_key"].replace('\\n', '\n')

            firebase_creds = {
                "type": st.secrets["firebase"]["type"],
                "project_id": st.secrets["firebase"]["project_id"],
                "private_key_id": st.secrets["firebase"]["private_key_id"],
                "private_key": firebase_private_key, # Use the modified private_key
                "client_email": st.secrets["firebase"]["client_email"],
                "client_id": st.secrets["firebase"]["client_id"],
                "auth_uri": st.secrets["firebase"]["auth_uri"],
                "token_uri": st.secrets["firebase"]["token_uri"],
                "auth_provider_x509_cert_url": st.secrets["firebase"]["auth_provider_x509_cert_url"],
                "client_x509_cert_url": st.secrets["firebase"]["client_x509_cert_url"],
                "universe_domain": st.secrets["firebase"]["universe_domain"],
            }
            cred = credentials.Certificate(firebase_creds)
            firebase_admin.initialize_app(cred, {"databaseURL": DATABASE_URL})
        except Exception as e:
            st.error(f"Error initializing Firebase. Check your `.streamlit/secrets.toml` and network connection. Ensure private_key is correctly formatted. Error: {e}", icon="❌")
            st.stop()

initialize_firebase()

# --- AUDIO PLAYBACK FUNCTION ---
def play_sound(sound_url: str, unique_key: str):
    """Embeds an HTML audio player that plays automatically."""
    # The audio player is hidden via CSS (display:none).
    # A unique key ensures that Streamlit re-renders the component on each call,
    # allowing the same sound to be played multiple times.
    audio_html = f"""
    <audio autoplay style="display:none;" key="{unique_key}">
      <source src="{sound_url}" type="audio/mpeg">
      Your browser does not support the audio element.
    </audio>
    """
    # Use st.components.v1.html to render the HTML. Height=0 is important.
    components.html(audio_html, height=0)


# --- FIREBASE DATA OPERATIONS ---
@st.cache_data(ttl=300, show_spinner="Loading your study tasks...")
def load_tasks():
    try:
        ref = db.reference(DB_PATH)
        data = ref.get()
        if not data:
            return [], [], [], set()

        tasks_list = []
        checks_list = []
        keys_list = []
        subjects_set = set()

        for key, value in data.items():
            task = value.get("task", {})
            check = value.get("check", {})
            tasks_list.append(task)
            checks_list.append(check)
            keys_list.append(key)
            if "Subject" in task:
                subjects_set.add(task["Subject"])

        return tasks_list, checks_list, keys_list, subjects_set
    except Exception as e:
        st.error(f"Error loading tasks from Firebase: {e}", icon="❌")
        return [], [], [], set()

def save_task(task, check, key=None):
    try:
        ref = db.reference(DB_PATH)
        if not key:
            key = str(uuid.uuid4())
        ref.child(key).set({"task": task, "check": check})
        return key
    except Exception as e:
        st.error(f"Error saving task to Firebase: {e}", icon="❌")
        return None

def delete_task_from_db(key):
    try:
        db.reference(DB_PATH).child(key).delete()
        return True
    except Exception as e:
        st.error(f"Error deleting task from Firebase: {e}", icon="❌")
        return False

# --- SESSION STATE INITIALIZATION ---
if "tasks" not in st.session_state:
    st.session_state.tasks, st.session_state.task_checks, st.session_state.task_keys, loaded_subjects = load_tasks()
    st.session_state.all_subjects = loaded_subjects

    if not st.session_state.all_subjects:
        st.session_state.all_subjects.add("Anatomy") # Ensure at least one subject exists

# Logic to set/restore selected_view_subject using URL query parameters
query_params = st.query_params

if "subject" in query_params:
    requested_subject = query_params["subject"]
    if requested_subject in st.session_state.all_subjects:
        st.session_state.selected_view_subject = requested_subject
    else:
        if st.session_state.all_subjects:
            st.session_state.selected_view_subject = sorted(list(st.session_state.all_subjects))[0]
        else:
            st.session_state.selected_view_subject = None
else:
    if "selected_view_subject" not in st.session_state or \
       (st.session_state.selected_view_subject not in st.session_state.all_subjects and st.session_state.all_subjects):
        if st.session_state.all_subjects:
            st.session_state.selected_view_subject = sorted(list(st.session_state.all_subjects))[0]
        else:
            st.session_state.selected_view_subject = None

if "editing_task_key" not in st.session_state:
    st.session_state.editing_task_key = None
if "temp_edit_task_data" not in st.session_state:
    st.session_state.temp_edit_task_data = {}

if "filter_start_date" not in st.session_state:
    st.session_state.filter_start_date = None
if "filter_end_date" not in st.session_state:
    st.session_state.filter_end_date = None
    
# --- Flags for playing sounds ---
if "play_pomodoro_finish_sound" not in st.session_state:
    st.session_state.play_pomodoro_finish_sound = False
if "play_tick_sound" not in st.session_state:
    st.session_state.play_tick_sound = False


# --- Pomodoro Timer Configuration & State Initialization ---
DEFAULT_WORK_MINS = 25
DEFAULT_BREAK_MINS = 5
DEFAULT_LONG_BREAK_MINS = 15

def init_pomodoro_state():
    if "pomodoro_work_mins" not in st.session_state:
        st.session_state.pomodoro_work_mins = DEFAULT_WORK_MINS
    if "pomodoro_break_mins" not in st.session_state:
        st.session_state.pomodoro_break_mins = DEFAULT_BREAK_MINS
    if "pomodoro_long_break_mins" not in st.session_state:
        st.session_state.pomodoro_long_break_mins = DEFAULT_LONG_BREAK_MINS
    if "pomodoro_running" not in st.session_state:
        st.session_state.pomodoro_running = False
    if "pomodoro_time_left" not in st.session_state:
        st.session_state.pomodoro_time_left = st.session_state.pomodoro_work_mins * 60
    if "pomodoro_mode" not in st.session_state:
        st.session_state.pomodoro_mode = "work"
    if "pomodoro_cycles" not in st.session_state:
        st.session_state.pomodoro_cycles = 0
    if "pomodoro_last_update_time" not in st.session_state:
        st.session_state.pomodoro_last_update_time = time.time()

init_pomodoro_state()

# --- Pomodoro Timer Functions ---
def update_timer_duration_on_edit():
    if st.session_state.pomodoro_mode == "work":
        st.session_state.pomodoro_time_left = st.session_state.pomodoro_work_mins * 60
    elif st.session_state.pomodoro_mode == "break":
        st.session_state.pomodoro_time_left = st.session_state.pomodoro_break_mins * 60
    elif st.session_state.pomodoro_mode == "long_break":
        st.session_state.pomodoro_time_left = st.session_state.pomodoro_long_break_mins * 60
    st.session_state.pomodoro_running = False

def start_pomodoro():
    st.session_state.pomodoro_running = True
    st.session_state.pomodoro_last_update_time = time.time()

def pause_pomodoro():
    st.session_state.pomodoro_running = False

def reset_pomodoro():
    st.session_state.pomodoro_running = False
    st.session_state.pomodoro_mode = "work"
    st.session_state.pomodoro_time_left = st.session_state.pomodoro_work_mins * 60
    st.session_state.pomodoro_cycles = 0
    st.session_state.pomodoro_last_update_time = time.time()

def toggle_mode():
    if st.session_state.pomodoro_mode == "work":
        st.session_state.pomodoro_cycles += 1
        if st.session_state.pomodoro_cycles % 4 == 0:
            st.session_state.pomodoro_mode = "long_break"
            st.session_state.pomodoro_time_left = st.session_state.pomodoro_long_break_mins * 60
        else:
            st.session_state.pomodoro_mode = "break"
            st.session_state.pomodoro_time_left = st.session_state.pomodoro_break_mins * 60
    else:
        st.session_state.pomodoro_mode = "work"
        st.session_state.pomodoro_time_left = st.session_state.pomodoro_work_mins * 60
    st.session_state.pomodoro_running = False
    st.session_state.pomodoro_last_update_time = time.time()

def format_time(seconds):
    mins, secs = divmod(int(seconds), 60)
    return f"{mins:02d}:{secs:02d}"

# --- LAYOUT & THEME ---
st.set_page_config("📚 Study Tracker", layout="wide", initial_sidebar_state="expanded")
st.title("📚 Study Tracker")

# --- CUSTOM CSS FOR BACKGROUND IMAGE AND COMPLETED TASKS & Pomodoro Timer ---
st.markdown("""
<style>
/* Existing styles remain the same... */
.stApp {
    background-image: url("YOUR_IMAGE_URL_HERE"); /* Replace with your image URL */
    background-size: cover;
    background-position: center;
    background-repeat: no-repeat;
    background-attachment: fixed;
}
.completed-task {
    border: 2px solid #28a745; border-radius: 5px; padding: 15px;
    margin-bottom: 20px; background-color: #e6ffe6;
    box-shadow: 0px 2px 4px rgba(0, 0, 0, 0.1);
}
body[data-theme="dark"] .completed-task { background-color: #1a472a; border-color: #3cb371; }
.stCheckbox > label { display: flex; align-items: center; }
.stCheckbox > label > div { margin-right: 5px; }
.main .block-container { background-color: rgba(255, 255, 255, 0.8); border-radius: 10px; padding: 20px; }
body[data-theme="dark"] .main .block-container { background-color: rgba(17, 26, 34, 0.85); }
.css-1d391kg { background-color: rgba(255, 255, 255, 0.85); border-radius: 10px; padding: 10px; }
body[data-theme="dark"] .css-1d391kg { background-color: rgba(17, 26, 34, 0.9); }
.pomodoro-container {
    background-color: #282c34; border-radius: 15px; padding: 20px; text-align: center;
    box-shadow: 0 4px 8px rgba(0, 0, 0, 0.3); margin-bottom: 20px; border: 1px solid #444;
}
body[data-theme="dark"] .pomodoro-container { background-color: #1a1a1a; border: 1px solid #333; box-shadow: 0 4px 10px rgba(0, 0, 0, 0.5); }
.pomodoro-mode-text { font-size: 1.2em; color: #a0a0a0; margin-bottom: 5px; }
.pomodoro-time-display {
    font-family: 'Space Mono', monospace; font-size: 4.5em; font-weight: bold; color: #61dafb;
    text-shadow: 0 0 10px rgba(97, 218, 251, 0.5); letter-spacing: 2px; margin: 15px 0;
}
body[data-theme="dark"] .pomodoro-time-display { color: #98fb98; text-shadow: 0 0 10px rgba(152, 251, 152, 0.5); }
.stButton button {
    background-color: #4CAF50; color: white; padding: 10px 20px; border: none; border-radius: 8px;
    cursor: pointer; font-size: 1em; margin: 5px; transition: background-color 0.3s ease, transform 0.1s ease;
}
.stButton button:hover { background-color: #45a049; transform: translateY(-2px); }
.stButton button:active { transform: translateY(0); }
.stButton button:disabled { background-color: #cccccc; cursor: not-allowed; }
body[data-theme="dark"] .stButton button { background-color: #3cb371; }
body[data-theme="dark"] .stButton button:hover { background-color: #2e8b57; }
/* White Noise Player CSS */
.white-noise-container {
    text-align: center;
    margin-top: 15px;
    padding-top: 15px;
    border-top: 1px solid #444;
}
.white-noise-container button {
    background-color: #555;
    padding: 8px 16px;
    font-size: 0.9em;
}
body[data-theme="dark"] .white-noise-container button { background-color: #333; }

</style>
""", unsafe_allow_html=True)


# --- SOUND TRIGGER ---
# This block checks flags and plays sounds. It's placed high to execute early on rerun.
if st.session_state.get("play_pomodoro_finish_sound", False):
    play_sound(POMODORO_FINISH_SOUND_URL, "pomodoro_finish")
    st.session_state.play_pomodoro_finish_sound = False # Reset flag

if st.session_state.get("play_tick_sound", False):
    play_sound(TASK_TICK_SOUND_URL, f"tick_{time.time()}") # Use time to ensure key is unique
    st.session_state.play_tick_sound = False # Reset flag


# --- WHITE NOISE PLAYER COMPONENT ---
def white_noise_player():
    """A self-contained HTML/JS component to play/pause white noise."""
    st.markdown('<div class="white-noise-container">', unsafe_allow_html=True)
    st.caption("Background White Noise")
    
    player_html = f"""
    <audio id="whiteNoisePlayer" loop>
        <source src="{WHITE_NOISE_URL}" type="audio/mpeg">
    </audio>

    <button id="playPauseBtn" onclick="togglePlay()">▶️ Play Noise</button>

    <script>
        var audio = document.getElementById("whiteNoisePlayer");
        var btn = document.getElementById("playPauseBtn");

        function togglePlay() {{
            if (audio.paused) {{
                audio.play();
                btn.innerHTML = "⏸️ Pause Noise";
            }} else {{
                audio.pause();
                btn.innerHTML = "▶️ Play Noise";
            }}
        }}
    </script>
    """
    components.html(player_html, height=50)
    st.markdown('</div>', unsafe_allow_html=True)


# --- Add Pomodoro Timer to Layout ---
def pomodoro_timer_section():
    st.markdown('<div class="pomodoro-container">', unsafe_allow_html=True)
    st.subheader("🍅 Pomodoro Timer")

    mode_display_placeholder = st.empty()
    timer_display_placeholder = st.empty()

    if st.session_state.pomodoro_running:
        elapsed_time = time.time() - st.session_state.pomodoro_last_update_time
        st.session_state.pomodoro_time_left -= elapsed_time
        st.session_state.pomodoro_last_update_time = time.time()

        if st.session_state.pomodoro_time_left <= 0:
            st.session_state.pomodoro_time_left = 0
            st.session_state.pomodoro_running = False
            
            # Set flag to play sound on the next rerun
            st.session_state.play_pomodoro_finish_sound = True

            st.success(f"{st.session_state.pomodoro_mode.replace('_', ' ').title()} session finished!", icon="✅")
            toggle_mode()
            st.rerun()

    mode_display_placeholder.markdown(f"<p class='pomodoro-mode-text'>Mode: **{st.session_state.pomodoro_mode.replace('_', ' ').title()}**</p>", unsafe_allow_html=True)
    timer_display_placeholder.markdown(f"<div class='pomodoro-time-display'>{format_time(st.session_state.pomodoro_time_left)}</div>", unsafe_allow_html=True)

    col_play_pause, col_reset, col_next, col_edit_toggle = st.columns(4)

    with col_play_pause:
        if st.session_state.pomodoro_running:
            if st.button("⏸️ Pause", key="pomodoro_pause_btn"):
                pause_pomodoro()
                st.rerun()
        else:
            if st.button("▶️ Start", key="pomodoro_start_btn"):
                start_pomodoro()
                st.rerun()
    with col_reset:
        if st.button("🔄 Reset", key="pomodoro_reset_btn"):
            reset_pomodoro()
            st.rerun()
    with col_next:
        if st.button("⏭️ Next Mode", key="pomodoro_toggle_btn"):
            toggle_mode()
            st.rerun()
    with col_edit_toggle:
        if st.button("⚙️ Edit Durations", key="edit_duration_toggle"):
            st.session_state.show_pomodoro_edit = not st.session_state.get("show_pomodoro_edit", False)
            st.rerun()

    if st.session_state.get("show_pomodoro_edit", False):
        st.markdown("---")
        st.markdown("#### Edit Timer Durations (in minutes)")
        col_edit1, col_edit2, col_edit3 = st.columns(3)
        with col_edit1:
            st.number_input("Work Time", min_value=1, max_value=120, value=st.session_state.pomodoro_work_mins, key="pomodoro_work_mins", on_change=update_timer_duration_on_edit)
        with col_edit2:
            st.number_input("Short Break", min_value=1, max_value=30, value=st.session_state.pomodoro_break_mins, key="pomodoro_break_mins", on_change=update_timer_duration_on_edit)
        with col_edit3:
            st.number_input("Long Break", min_value=1, max_value=60, value=st.session_state.pomodoro_long_break_mins, key="pomodoro_long_break_mins", on_change=update_timer_duration_on_edit)
    
    # Add the white noise player inside the Pomodoro container
    white_noise_player()

    st.markdown('</div>', unsafe_allow_html=True)

    if st.session_state.pomodoro_running:
        time.sleep(1)
        st.rerun()

# --- ADD NEW TASK FORM (No changes here) ---
def add_task_form():
    with st.sidebar.expander("➕ Add New Task", expanded=True):
        current_subjects_list = sorted(list(st.session_state.all_subjects))
        subject_options = ["➕ Add new subject"] + current_subjects_list
        
        chapter_value = st.session_state.get("chapter_input_val", "")
        sn_value = st.session_state.get("sn_input_val", "")
        laq_value = st.session_state.get("laq_input_val", "")
        new_subject_value = st.session_state.get("new_subject_input_val", "")

        selected_subject_input = st.selectbox(
            "Subject", 
            subject_options, 
            key="add_subject_select",
            index=0
        )

        subject_to_add = ""
        if selected_subject_input == "➕ Add new subject":
            new_subject_text = st.text_input("Enter new subject name", value=new_subject_value, key="new_subject_input")
            subject_to_add = new_subject_text
        else:
            subject_to_add = selected_subject_input

        chapter_input = st.text_input("Chapter", value=chapter_value, key="chapter_input")
        sn_input = st.text_area("Short Notes (SN), one per line", value=sn_value, key="sn_input")
        laq_input = st.text_area("Long Answer Questions (LAQ), one per line", value=laq_value, key="laq_input")
        
        sn_list = [l.strip() for l in sn_input.splitlines() if l.strip()]
        laq_list = [l.strip() for l in laq_input.splitlines() if l.strip()]

        priority_input = st.selectbox("Priority", ["High","Medium","Low"], key="priority_input")
        deadline_input = st.date_input("Deadline", min_value=date.today(), key="deadline_input")

        if st.button("Add Task", key="add_task_button"):
            cleaned_subject = subject_to_add.strip()
            cleaned_chapter = chapter_input.strip()

            if not cleaned_subject:
                st.warning("Please enter a subject name.")
            elif not cleaned_chapter:
                st.warning("Please enter a Chapter name.")
            elif not sn_list and not laq_list:
                st.warning("Enter at least one Short Note or Long Answer Question.")
            else:
                task = {
                    "Subject": cleaned_subject, "Chapter": cleaned_chapter,
                    "SN": sn_list, "LAQ": laq_list,
                    "Priority": priority_input, "Deadline": str(deadline_input)
                }
                check = {"SN": [False] * len(sn_list), "LAQ": [False] * len(laq_list)}
                
                key = save_task(task, check)
                if key:
                    st.session_state.tasks.append(task)
                    st.session_state.task_checks.append(check)
                    st.session_state.task_keys.append(key)
                    st.session_state.all_subjects.add(cleaned_subject)
                    
                    st.success(f"Task '{cleaned_chapter}' added successfully!", icon="✅")
                    
                    st.session_state.chapter_input_val, st.session_state.sn_input_val, st.session_state.laq_input_val, st.session_state.new_subject_input_val = "", "", "", ""
                    
                    st.rerun()
                else:
                    st.error("Failed to add task. Please try again.", icon="❌")

# --- FILTER AND SEARCH (No changes here) ---
def filter_and_search_options():
    st.header("🔍 Filter & Search")
    col_priority, col_search = st.columns([0.5, 0.5])
    with col_priority:
        st.session_state.filter_priorities = st.multiselect("Filter by Priority", options=["High", "Medium", "Low"], default=["High", "Medium", "Low"], key="priority_filter")
    with col_search:
        st.session_state.search_query = st.text_input("Search Tasks", placeholder="Search by chapter, notes, or questions...", key="search_input")
    col_start_date, col_end_date = st.columns([0.5, 0.5])
    with col_start_date:
        st.date_input("Start Date (Deadline)", value=st.session_state.filter_start_date, min_value=None, key="filter_start_date")
    with col_end_date:
        min_end_date = st.session_state.filter_start_date if st.session_state.filter_start_date else None
        st.date_input("End Date (Deadline)", value=st.session_state.filter_end_date, min_value=min_end_date, key="filter_end_date")
    st.divider()

# --- MAIN CONTENT (FIX APPLIED HERE) ---
def subject_filter_section():
    current_display_subjects = sorted(list(st.session_state.all_subjects))
    if not current_display_subjects:
        st.warning("No subjects available yet. Add a task to create subjects.")
        st.session_state.selected_view_subject = None
        return
    
    initial_index = 0
    if st.session_state.selected_view_subject in current_display_subjects:
        initial_index = current_display_subjects.index(st.session_state.selected_view_subject)
    
    # The 'on_change' callback is now the single source of truth for handling the change.
    # We assign its value back to session state within the callback function.
    st.selectbox(
        "📘 Select Subject to View", current_display_subjects,
        key="view_subject_select", 
        index=initial_index,
        on_change=update_subject_query_param,
        args=(st.session_state,) # Pass session_state to the callback
    )

def update_subject_query_param(session_state_ref):
    # This callback now handles both updating the URL and session state
    new_subject = session_state_ref.view_subject_select
    session_state_ref.selected_view_subject = new_subject
    st.query_params["subject"] = new_subject


def get_filtered_tasks():
    filtered_tasks = []
    search_lower = st.session_state.search_query.lower() if st.session_state.get('search_query') else ""
    start_date_filter, end_date_filter = st.session_state.get('filter_start_date'), st.session_state.get('filter_end_date')
    for i, task in enumerate(st.session_state.tasks):
        if st.session_state.selected_view_subject is None or task.get("Subject") != st.session_state.selected_view_subject:
            continue
        if st.session_state.get('filter_priorities') and task.get("Priority") not in st.session_state.filter_priorities:
            continue
        if search_lower:
            match_found = (search_lower in task.get("Chapter", "").lower() or
                           any(search_lower in sn.lower() for sn in task.get("SN", [])) or
                           any(search_lower in laq.lower() for laq in task.get("LAQ", [])))
            if not match_found:
                continue
        task_deadline_str = task.get("Deadline")
        if task_deadline_str:
            try:
                task_deadline = date.fromisoformat(task_deadline_str)
                if start_date_filter and task_deadline < start_date_filter: continue
                if end_date_filter and task_deadline > end_date_filter: continue
            except ValueError: pass
        elif start_date_filter or end_date_filter:
            continue
        filtered_tasks.append((i, task, st.session_state.task_checks[i], st.session_state.task_keys[i]))
    return filtered_tasks

def completion_overview_section():
    st.header("📈 Completion Overview")
    if st.session_state.selected_view_subject is None:
        st.info("No subjects to display completion overview.")
        return
    filtered_tasks_data = get_filtered_tasks()
    if not filtered_tasks_data:
        st.info(f"No tasks found for the selected subject and current filters.")
        return
    for i, task, checks, key_fk in filtered_tasks_data:
        sn_done, laq_done = sum(checks.get("SN", [])), sum(checks.get("LAQ", []))
        total_sn, total_laq = len(task.get("SN", [])), len(task.get("LAQ", []))
        total_items, items_done = total_sn + total_laq, sn_done + laq_done
        pct = int((items_done / total_items * 100)) if total_items else 0
        st.markdown(f"""
        <a href="#task-{key_fk}" style="text-decoration: none; color: inherit;">
            <div style="cursor: pointer;">{task['Chapter']}: {pct}% done ({items_done}/{total_items})</div>
        </a>""", unsafe_allow_html=True)
        st.progress(pct / 100)

# --- EDIT FORM (No changes here) ---
def display_edit_form(current_task_data, current_task_checks, current_key_fk):
    st.subheader(f"✏️ Editing: {current_task_data['Chapter']}")
    all_subjects_for_edit = sorted(list(st.session_state.all_subjects))
    if current_task_data.get("Subject") not in all_subjects_for_edit:
        all_subjects_for_edit.append(current_task_data.get("Subject", ""))
        all_subjects_for_edit.sort()
    try: current_subject_index = all_subjects_for_edit.index(current_task_data.get("Subject", ""))
    except ValueError: current_subject_index = 0
    edited_subject = st.selectbox("Subject", all_subjects_for_edit, index=current_subject_index, key=f"edit_subject_{current_key_fk}")
    edited_chapter = st.text_input("Chapter", value=current_task_data.get("Chapter", ""), key=f"edit_chapter_{current_key_fk}")
    edited_sn = st.text_area("Short Notes (SN)", value="\n".join(current_task_data.get("SN", [])), key=f"edit_sn_{current_key_fk}")
    edited_laq = st.text_area("Long Answer Questions (LAQ)", value="\n".join(current_task_data.get("LAQ", [])), key=f"edit_laq_{current_key_fk}")
    priority_options = ["High", "Medium", "Low"]
    try: current_priority_index = priority_options.index(current_task_data.get("Priority", "Medium"))
    except ValueError: current_priority_index = 1
    edited_priority = st.selectbox("Priority", priority_options, index=current_priority_index, key=f"edit_priority_{current_key_fk}")
    try: current_deadline_date_obj = date.fromisoformat(current_task_data.get("Deadline", str(date.today())))
    except ValueError: current_deadline_date_obj = date.today()
    initial_deadline_value = max(current_deadline_date_obj, date.today())
    edited_deadline = st.date_input("Deadline", value=initial_deadline_value, min_value=date.today(), key=f"edit_deadline_{current_key_fk}")
    col_save, col_cancel = st.columns([0.15, 1])
    with col_save:
        if st.button("💾 Save Changes", key=f"save_edit_{current_key_fk}"):
            new_sn_list = [l.strip() for l in edited_sn.splitlines() if l.strip()]
            new_laq_list = [l.strip() for l in edited_laq.splitlines() if l.strip()]
            if not edited_chapter.strip():
                st.error("Chapter cannot be empty.", icon="❌")
            elif not new_sn_list and not new_laq_list:
                st.error("At least one Short Note or Long Answer Question is required.", icon="❌")
            else:
                new_checks_sn = [False] * len(new_sn_list)
                for i, sn_item in enumerate(new_sn_list):
                    try: new_checks_sn[i] = current_task_checks["SN"][current_task_data["SN"].index(sn_item)]
                    except (ValueError, KeyError, IndexError): pass
                new_checks_laq = [False] * len(new_laq_list)
                for i, laq_item in enumerate(new_laq_list):
                    try: new_checks_laq[i] = current_task_checks["LAQ"][current_task_data["LAQ"].index(laq_item)]
                    except (ValueError, KeyError, IndexError): pass
                updated_task = {"Subject": edited_subject.strip(), "Chapter": edited_chapter.strip(), "SN": new_sn_list, "LAQ": new_laq_list, "Priority": edited_priority, "Deadline": str(edited_deadline)}
                updated_checks = {"SN": new_checks_sn, "LAQ": new_checks_laq}
                with st.spinner("Saving changes..."):
                    if save_task(updated_task, updated_checks, key=current_key_fk):
                        st.cache_data.clear()
                        st.session_state.tasks, st.session_state.task_checks, st.session_state.task_keys, st.session_state.all_subjects = load_tasks()
                        st.session_state.editing_task_key = None
                        st.session_state.temp_edit_task_data = {}
                        st.success(f"Task '{updated_task['Chapter']}' updated successfully!", icon="✅")
                        st.rerun()
                    else: st.error("Failed to save changes. Please try again.", icon="❌")
    with col_cancel:
        if st.button("❌ Cancel Edit", key=f"cancel_edit_{current_key_fk}"):
            st.session_state.editing_task_key = None
            st.session_state.temp_edit_task_data = {}
            st.rerun()

# --- Display Task List (with Tick Sound) ---
def task_list_section():
    st.header("🗂️ Task List")

    if st.session_state.selected_view_subject is None:
        st.info("No subjects to display tasks.")
        return

    filtered_tasks_data = get_filtered_tasks()

    if not filtered_tasks_data:
        st.info(f"No tasks found for the selected subject and current filters/search query.")
        return

    def sort_key_func(item):
        task = item[1]
        try: deadline = date.fromisoformat(task.get("Deadline", "9999-12-31"))
        except ValueError: deadline = date.max
        priority = {"High": 0, "Medium": 1, "Low": 2}.get(task.get("Priority", "Medium"), 1)
        return (deadline, priority)

    filtered_tasks_data.sort(key=sort_key_func)

    for original_idx, task, checks, key_fk in filtered_tasks_data:
        total_sn, total_laq = len(task.get("SN", [])), len(task.get("LAQ", []))
        total_items = total_sn + total_laq
        sn_checked_count, laq_checked_count = sum(checks.get("SN", [])), sum(checks.get("LAQ", []))
        is_completed = (total_items > 0) and (sn_checked_count == total_sn) and (laq_checked_count == total_laq)
        task_container_class = "task-item completed-task" if is_completed else "task-item"
        st.markdown(f'<div id="task-{key_fk}" class="{task_container_class}">', unsafe_allow_html=True)

        items_done = sn_checked_count + laq_checked_count
        pct = int((items_done / total_items * 100)) if total_items else 0

        if st.session_state.editing_task_key == key_fk:
            display_edit_form(task, checks, key_fk)
        else:
            st.markdown(f"### {task['Chapter']} ({task.get('Priority')} Priority, Due: {task.get('Deadline')})")
            st.progress(pct / 100, text=f"{pct}% done ({items_done}/{total_items})")
            col1, col2, col3, col4 = st.columns([1, 1, 0.2, 0.2])
            with col1:
                if task.get("SN"):
                    st.markdown("**📝 Short Notes**")
                    for j, t in enumerate(task["SN"]):
                        if st.checkbox(t, key=f"sn_{key_fk}_{j}", value=checks["SN"][j]):
                            if not checks["SN"][j]:
                                checks["SN"][j] = True
                                save_task(task, checks, key=key_fk)
                                st.session_state.play_tick_sound = True # Set flag to play sound
                                st.rerun()
                        elif checks["SN"][j]:
                            checks["SN"][j] = False
                            save_task(task, checks, key=key_fk)
                            st.rerun()
            with col2:
                if task.get("LAQ"):
                    st.markdown("**📄 Long Answer Questions**")
                    for j, t in enumerate(task["LAQ"]):
                        if st.checkbox(t, key=f"laq_{key_fk}_{j}", value=checks["LAQ"][j]):
                            if not checks["LAQ"][j]:
                                checks["LAQ"][j] = True
                                save_task(task, checks, key=key_fk)
                                st.session_state.play_tick_sound = True # Set flag to play sound
                                st.rerun()
                        elif checks["LAQ"][j]:
                            checks["LAQ"][j] = False
                            save_task(task, checks, key=key_fk)
                            st.rerun()
            with col3:
                st.markdown("<br>", unsafe_allow_html=True)
                if st.button("✏️ Edit", key=f"edit_btn_{key_fk}"):
                    st.session_state.editing_task_key = key_fk
                    st.session_state.temp_edit_task_data = task
                    st.rerun()
            with col4:
                st.markdown("<br>", unsafe_allow_html=True)
                if st.button("🗑️ Delete", key=f"del_btn_{key_fk}"):
                    st.session_state[f"show_confirm_{key_fk}"] = True
                    st.rerun()

        if st.session_state.get(f"show_confirm_{key_fk}", False):
            st.warning(f"Are you sure you want to delete chapter '{task['Chapter']}'?", icon="⚠️")
            col_yes, col_no = st.columns([0.1, 1])
            with col_yes:
                if st.button("Yes, Delete", key=f"confirm_del_yes_{key_fk}"):
                    with st.spinner(f"Deleting '{task['Chapter']}'..."):
                        if delete_task_from_db(key_fk):
                            st.session_state.last_deleted = (task, checks, key_fk)
                            st.cache_data.clear()
                            st.session_state.tasks, st.session_state.task_checks, st.session_state.task_keys, st.session_state.all_subjects = load_tasks()
                            st.success(f"Task '{task['Chapter']}' deleted successfully!", icon="✅")
                            st.session_state[f"show_confirm_{key_fk}"] = False
                            st.rerun()
                        else: st.error(f"Failed to delete '{task['Chapter']}'. Please try again.", icon="❌")
            with col_no:
                if st.button("No, Cancel", key=f"confirm_del_no_{key_fk}"):
                    st.session_state[f"show_confirm_{key_fk}"] = False
                    st.rerun()

        st.markdown('</div>', unsafe_allow_html=True)
        st.divider()

# --- UNDO DELETE / EXPORT (No changes here) ---
def undo_delete_section():
    if "last_deleted" in st.session_state and st.session_state.last_deleted is not None:
        if st.button("↩️ Undo Last Delete", key="undo_delete_button"):
            task_to_restore, checks_to_restore, key_to_restore = st.session_state.last_deleted
            with st.spinner("Restoring task..."):
                if save_task(task_to_restore, checks_to_restore, key_to_restore):
                    st.cache_data.clear()
                    st.session_state.tasks, st.session_state.task_checks, st.session_state.task_keys, st.session_state.all_subjects = load_tasks()
                    st.session_state.last_deleted = None
                    st.success("Task restored successfully!", icon="✅")
                    st.rerun()
                else: st.error("Failed to undo delete. Please try again.", icon="❌")

def export_csv_section():
    st.header("⬇️ Export Tasks")
    rows = []
    for i, task in enumerate(st.session_state.tasks):
        if task.get("Subject") == st.session_state.selected_view_subject:
            checks = st.session_state.task_checks[i]
            for j, t in enumerate(task.get("SN",[])):
                rows.append([task["Subject"],task["Chapter"],"SN",t,task["Priority"],task["Deadline"], "Done" if checks["SN"][j] else "Pending"])
            for j, t in enumerate(task.get("LAQ",[])):
                rows.append([task["Subject"],task["Chapter"],"LAQ",t,task["Priority"],task["Deadline"], "Done" if checks["LAQ"][j] else "Pending"])
    if rows:
        df = pd.DataFrame(rows, columns=["Subject","Chapter","Type","Task","Priority","Deadline", "Status"])
        csv = df.to_csv(index=False).encode()
        st.download_button("Export Current Subject's Tasks to CSV", csv, f"{st.session_state.selected_view_subject}_tasks.csv", "text/csv")
    else: st.info("No tasks to export for the selected subject.")

# --- Render Sections ---
add_task_form()
st.sidebar.divider()
undo_delete_section()

filter_and_search_options()
pomodoro_timer_section()
st.divider()
subject_filter_section()
completion_overview_section()
st.divider()
task_list_section()
st.divider()
export_csv_section()
