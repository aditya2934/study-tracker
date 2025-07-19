import streamlit as st
import firebase_admin
from firebase_admin import credentials, db, auth
from datetime import date
import uuid
import pandas as pd
import json
import time
import streamlit.components.v1 as components

# --- CONFIGURATION (using Streamlit Secrets) ---
DB_PATH_ROOT = "users"

# --- AUDIO ASSETS (URLs) ---
POMODORO_FINISH_SOUND_URL = "https://www.soundjay.com/buttons/sounds/button-16.mp3"
TASK_TICK_SOUND_URL = "https://www.soundjay.com/buttons/sounds/button-48.mp3"
WHITE_NOISE_URL = "https://www.soundjay.com/nature/sounds/whitenoise-1.mp3"

# --- FIREBASE ADMIN SDK INITIALIZATION ---
@st.cache_resource(show_spinner="Initializing Firebase Admin SDK...")
def initialize_firebase_admin_sdk():
    if firebase_admin._apps:
        return
    try:
        firebase_config = st.secrets["firebase"]
        firebase_private_key = firebase_config["private_key"].replace('\\n', '\n')
        cred_dict = {
            "type": firebase_config["type"],
            "project_id": firebase_config["project_id"],
            "private_key_id": firebase_config["private_key_id"],
            "private_key": firebase_private_key,
            "client_email": firebase_config["client_email"],
            "client_id": firebase_config["client_id"],
            "auth_uri": firebase_config["auth_uri"],
            "token_uri": firebase_config["token_uri"],
            "auth_provider_x509_cert_url": firebase_config["auth_provider_x509_cert_url"],
            "client_x509_cert_url": firebase_config["client_x509_cert_url"],
            "universe_domain": firebase_config.get("universe_domain", "googleapis.com"),
        }
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred, {"databaseURL": firebase_config["database_url"]})
    except Exception as e:
        st.error(f"Error initializing Firebase Admin SDK: {e}", icon="❌")
        st.stop()

initialize_firebase_admin_sdk()

# --- CLIENT-SIDE FIREBASE SDK CONFIG ---
try:
    FIREBASE_CLIENT_CONFIG = json.dumps(st.secrets["firebase_client"])
except (KeyError, json.JSONDecodeError):
    st.error("Client-side Firebase configuration is missing or invalid in secrets.", icon="❌")
    st.stop()

# --- SESSION STATE INITIALIZATION FOR AUTHENTICATION ---
if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "user_email" not in st.session_state:
    st.session_state.user_email = None
if "auth_status" not in st.session_state:
    st.session_state.auth_status = "pending" # pending, logged_in, logged_out

# --- AUTHENTICATION COMPONENT ---
def firebase_auth_component():
    html_code = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <script src="https://www.gstatic.com/firebasejs/9.6.1/firebase-app-compat.js"></script>
        <script src="https://www.gstatic.com/firebasejs/9.6.1/firebase-auth-compat.js"></script>
        <script>
            const firebaseConfig = JSON.parse(`{FIREBASE_CLIENT_CONFIG}`);
            if (!firebase.apps.length) {{ firebase.initializeApp(firebaseConfig); }}

            const auth = firebase.auth();
            const provider = new firebase.auth.GoogleAuthProvider();

            function updateStreamlit(status, userData = null) {{
                if (window.Streamlit) {{
                    Streamlit.setComponentValue({{ status: status, userData: userData }});
                }}
            }}

            auth.onAuthStateChanged(user => {{
                const signInButton = document.getElementById('signInButton');
                const signOutButton = document.getElementById('signOutButton');
                if (user) {{
                    if (signInButton) signInButton.style.display = 'none';
                    if (signOutButton) signOutButton.style.display = 'block';
                    updateStreamlit('logged_in', {{ uid: user.uid, email: user.email }});
                }} else {{
                    if (signInButton) signInButton.style.display = 'block';
                    if (signOutButton) signOutButton.style.display = 'none';
                    updateStreamlit('logged_out', null);
                }}
            }});

            window.signInWithGoogle = () => auth.signInWithPopup(provider).catch(e => updateStreamlit('error', e.message));
            window.signOutUser = () => auth.signOut().catch(e => updateStreamlit('error', e.message));

            document.addEventListener('DOMContentLoaded', () => {{
                setTimeout(() => {{
                    if(window.Streamlit) {{
                       Streamlit.setComponentReady();
                       // Send initial state
                       if (auth.currentUser) {{
                           updateStreamlit('logged_in', {{ uid: auth.currentUser.uid, email: auth.currentUser.email }});
                       }} else {{
                           updateStreamlit('logged_out', null);
                       }}
                    }}
                }}, 100);
            }});
        </script>
        <style>
            .auth-buttons button {{
                background-color: #4285F4; color: white; padding: 10px 20px;
                border: none; border-radius: 5px; cursor: pointer;
                font-size: 16px; margin: 5px;
            }}
            .auth-buttons button:hover {{ background-color: #357ae8; }}
        </style>
    </head>
    <body>
        <div class="auth-buttons">
            <button id="signInButton" onclick="signInWithGoogle()">Sign in with Google</button>
            <button id="signOutButton" onclick="signOutUser()" style="display: none;">Sign Out</button>
        </div>
    </body>
    </html>
    """
    return components.html(html_code, height=50)

# --- SINGLE POINT FOR AUTH COMPONENT RENDERING & STATE PROCESSING ---
# This is the core fix: Render once, process state, then build the UI.
auth_component_data = firebase_auth_component()

if auth_component_data:
    status = auth_component_data.get("status")
    userData = auth_component_data.get("userData")

    # If component reports a state different from the app's state, update and rerun
    if status == "logged_in" and (st.session_state.auth_status != "logged_in" or st.session_state.user_id != userData.get("uid")):
        st.session_state.auth_status = "logged_in"
        st.session_state.user_id = userData.get("uid")
        st.session_state.user_email = userData.get("email")
        st.rerun()

    elif status == "logged_out" and st.session_state.auth_status != "logged_out":
        st.session_state.auth_status = "logged_out"
        st.session_state.user_id = None
        st.session_state.user_email = None
        st.rerun()
    
    elif status == "error" and st.session_state.auth_status != "logged_out":
        st.error(f"Authentication Error: {userData}", icon="❌")
        st.session_state.auth_status = "logged_out" # Force logout on error
        st.session_state.user_id = None
        st.session_state.user_email = None
        st.rerun()

# --- THE REST OF THE SCRIPT (UNCHANGED LOGIC, JUST RELIES ON THE STATE SET ABOVE) ---
# --- Functions to interact with Firebase Realtime Database (USER SPECIFIC) ---
@st.cache_data(ttl=300, show_spinner="Loading your study tasks...")
def load_tasks_for_user(user_id):
    if not user_id: return [], [], [], set()
    try:
        ref = db.reference(f"{DB_PATH_ROOT}/{user_id}/tasks")
        data = ref.get() or {}
        tasks_list, checks_list, keys_list, subjects_set = [], [], [], set()
        for key, value in data.items():
            tasks_list.append(value.get("task", {}))
            checks_list.append(value.get("check", {}))
            keys_list.append(key)
            if "Subject" in value.get("task", {}):
                subjects_set.add(value["task"]["Subject"])
        return tasks_list, checks_list, keys_list, subjects_set
    except Exception as e:
        st.error(f"Error loading tasks: {e}", icon="❌")
        return [], [], [], set()

def save_task_for_user(user_id, task, check, key=None):
    if not user_id:
        st.warning("Cannot save task: No user logged in.")
        return None
    try:
        ref = db.reference(f"{DB_PATH_ROOT}/{user_id}/tasks")
        key = key or str(uuid.uuid4())
        ref.child(key).set({"task": task, "check": check})
        return key
    except Exception as e:
        st.error(f"Error saving task: {e}", icon="❌")
        return None

def delete_task_from_db_for_user(user_id, key):
    if not user_id:
        st.warning("Cannot delete task: No user logged in.")
        return False
    try:
        db.reference(f"{DB_PATH_ROOT}/{user_id}/tasks").child(key).delete()
        return True
    except Exception as e:
        st.error(f"Error deleting task: {e}", icon="❌")
        return False

# --- AUDIO PLAYBACK FUNCTION ---
def play_sound(sound_url: str):
    components.html(f'<audio autoplay><source src="{sound_url}"></audio>', height=0)

# --- MAIN APP LAYOUT (Conditional based on Authentication) ---
if st.session_state.auth_status == "logged_in":
    st.sidebar.success(f"Logged in as: {st.session_state.user_email}", icon="✅")
    
    # Initialize app-specific state only when logged in
    if "tasks" not in st.session_state:
        st.session_state.tasks, st.session_state.task_checks, st.session_state.task_keys, loaded_subjects = load_tasks_for_user(st.session_state.user_id)
        st.session_state.all_subjects = loaded_subjects or {"Anatomy"}
    
    # ... (the rest of your application code remains the same) ...
    # (I've omitted the long UI code for brevity, but you should paste it here)

    # --- LAYOUT & THEME ---
    st.set_page_config("📚 Study Tracker", layout="wide", initial_sidebar_state="expanded")
    st.title("📚 Study Tracker")

    # --- CUSTOM CSS ---
    st.markdown("""
    <style>
    /* Paste all your CSS styles here... */
    .completed-task { border: 2px solid #28a745; }
    </style>
    """, unsafe_allow_html=True)
    
    # --- SOUND TRIGGER ---
    if st.session_state.get("play_pomodoro_finish_sound", False):
        play_sound(POMODORO_FINISH_SOUND_URL)
        st.session_state.play_pomodoro_finish_sound = False

    if st.session_state.get("play_tick_sound", False):
        play_sound(TASK_TICK_SOUND_URL)
        st.session_state.play_tick_sound = False
        
    # --- PASTE ALL YOUR UI FUNCTIONS HERE ---
    # e.g., pomodoro_timer_section(), add_task_form(), task_list_section(), etc.
    # Make sure to define them before calling them.
    
    # A placeholder for your UI functions
    def build_main_ui():
        st.write("Main Application UI for logged-in user.")
        # add_task_form()
        # pomodoro_timer_section()
        # ... and so on
    
    build_main_ui()


elif st.session_state.auth_status == "logged_out":
    st.warning("Please sign in to use the application.")
    # The login button is already rendered at the top.

else: # pending
    st.info("Checking authentication status...")
    # The component is already rendered at the top, waiting for a callback.
