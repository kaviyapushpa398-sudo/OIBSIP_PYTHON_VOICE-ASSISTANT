"""
╔══════════════════════════════════════════════════════════════╗
║           ARIA — Advanced Recognition & Interaction Agent    ║
║  Voice assistant with NLP, weather, email, reminders & more  ║
╚══════════════════════════════════════════════════════════════╝

Dependencies:
    pip install SpeechRecognition pyttsx3 requests pyaudio wikipedia-api

Note: PyAudio may require OS-level install first:
    Linux  : sudo apt install portaudio19-dev && pip install pyaudio
    Mac    : brew install portaudio && pip install pyaudio
    Windows: pip install pipwin && pipwin install pyaudio
"""

# ── Standard library ─────────────────────────────────────────────────────────
import threading
import datetime
import webbrowser
import smtplib
import json
import re
import os
import time
import queue
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ── Third-party ───────────────────────────────────────────────────────────────
import tkinter as tk
from tkinter import ttk, scrolledtext, simpledialog, messagebox

try:
    import speech_recognition as sr
    SR_AVAILABLE = True
except ImportError:
    SR_AVAILABLE = False

try:
    import pyttsx3
    TTS_AVAILABLE = True
except ImportError:
    TTS_AVAILABLE = False

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION  (edit these or expose via settings dialog)
# ══════════════════════════════════════════════════════════════════════════════
CONFIG = {
    "wake_word"         : "aria",
    "voice_rate"        : 175,          # words per minute
    "voice_volume"      : 0.9,
    "weather_city"      : "London",     # default city
    "email_sender"      : "",           # your Gmail address
    "email_password"    : "",           # app-password (NOT real password)
    "custom_commands"   : {},           # {"phrase": "response"}
    "reminders"         : [],           # [{text, due_time}]
}

# ══════════════════════════════════════════════════════════════════════════════
#  CORE ENGINE
# ══════════════════════════════════════════════════════════════════════════════
class AriaEngine:
    """All assistant logic lives here — GUI-independent."""

    def __init__(self, log_cb, status_cb):
        self.log_cb    = log_cb      # callable(text, tag)
        self.status_cb = status_cb   # callable(text)
        self.listening = False
        self.tts_engine = None
        self._init_tts()
        self._reminder_thread = threading.Thread(
            target=self._reminder_loop, daemon=True)
        self._reminder_thread.start()

    # ── TTS ──────────────────────────────────────────────────────────────────
    def _init_tts(self):
        if TTS_AVAILABLE:
            try:
                self.tts_engine = pyttsx3.init()
                self.tts_engine.setProperty("rate",   CONFIG["voice_rate"])
                self.tts_engine.setProperty("volume", CONFIG["voice_volume"])
                voices = self.tts_engine.getProperty("voices")
                # Prefer a female voice if available
                for v in voices:
                    if "female" in v.name.lower() or "zira" in v.name.lower():
                        self.tts_engine.setProperty("voice", v.id)
                        break
            except Exception:
                self.tts_engine = None

    def speak(self, text: str):
        self.log_cb(f"ARIA ▸ {text}", "aria")
        if self.tts_engine:
            try:
                self.tts_engine.say(text)
                self.tts_engine.runAndWait()
            except Exception:
                pass  # silently degrade if audio device unavailable

    # ── Speech Recognition ────────────────────────────────────────────────────
    def listen_once(self) -> str | None:
        if not SR_AVAILABLE:
            return None
        recognizer = sr.Recognizer()
        recognizer.pause_threshold = 0.8
        self.status_cb("🎙  Listening…")
        try:
            with sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = recognizer.listen(source, timeout=5, phrase_time_limit=10)
            self.status_cb("🔍  Recognising…")
            text = recognizer.recognize_google(audio)
            self.log_cb(f"YOU ▸ {text}", "user")
            return text.lower()
        except sr.WaitTimeoutError:
            self.status_cb("⏳  No speech detected")
            return None
        except sr.UnknownValueError:
            self.status_cb("❓  Could not understand")
            return None
        except sr.RequestError as e:
            self.status_cb(f"❌  API error: {e}")
            return None
        except Exception as e:
            self.status_cb(f"❌  Mic error: {e}")
            return None
        finally:
            self.status_cb("💤  Idle")

    # ── Intent Dispatch ───────────────────────────────────────────────────────
    def process(self, text: str) -> str:
        t = text.lower().strip()

        # Custom commands (highest priority)
        for phrase, response in CONFIG["custom_commands"].items():
            if phrase.lower() in t:
                return response

        # Greetings
        if any(w in t for w in ["hello", "hi", "hey", "greetings"]):
            hour = datetime.datetime.now().hour
            greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
            return f"{greeting}! I'm ARIA, your voice assistant. How can I help?"

        # Time & Date
        if "time" in t:
            return "The current time is " + datetime.datetime.now().strftime("%I:%M %p")
        if "date" in t or "today" in t:
            return "Today is " + datetime.datetime.now().strftime("%A, %B %d, %Y")

        # Day of week
        if "day" in t:
            return "Today is " + datetime.datetime.now().strftime("%A")

        # Weather
        if "weather" in t:
            city = self._extract_city(t) or CONFIG["weather_city"]
            return self._get_weather(city)

        # Reminders
        if "remind" in t or "reminder" in t:
            return self._set_reminder(t)
        if "show reminder" in t or "my reminder" in t:
            return self._list_reminders()

        # Email
        if "send email" in t or "send mail" in t:
            return self._send_email_prompt(t)

        # Web search
        if "search" in t or "look up" in t or "google" in t:
            query = re.sub(r"(search|look up|google|for|about)", "", t).strip()
            return self._web_search(query)

        # Open website
        if "open" in t and ("website" in t or "site" in t or ".com" in t or ".org" in t):
            return self._open_url(t)

        # Wikipedia
        if "who is" in t or "what is" in t or "tell me about" in t:
            return self._wiki_summary(t)

        # Math / calculation
        if "calculate" in t or "what is" in t:
            return self._calculate(t)

        # Jokes
        if "joke" in t:
            return self._tell_joke()

        # System
        if "stop" in t or "quit" in t or "exit" in t or "goodbye" in t:
            return "STOP"

        if "help" in t:
            return self._help_text()

        return ("I'm not sure how to help with that. Say 'help' for a list of commands, "
                "or try asking about the time, weather, or to search the web.")

    # ── Skills ────────────────────────────────────────────────────────────────
    def _extract_city(self, text: str) -> str | None:
        m = re.search(r"weather (?:in|at|for) ([a-z ]+)", text)
        return m.group(1).strip().title() if m else None

    def _get_weather(self, city: str) -> str:
        if not REQUESTS_AVAILABLE:
            return "requests library not installed — cannot fetch weather."
        try:
            url = f"https://wttr.in/{city}?format=j1"
            r = requests.get(url, timeout=6)
            data = r.json()
            cc   = data["current_condition"][0]
            desc = cc["weatherDesc"][0]["value"]
            temp_c = cc["temp_C"]
            temp_f = cc["temp_F"]
            feels = cc["FeelsLikeC"]
            humid = cc["humidity"]
            return (f"In {city}: {desc}. Temperature {temp_c}°C ({temp_f}°F), "
                    f"feels like {feels}°C, humidity {humid}%.")
        except Exception as e:
            return f"Couldn't fetch weather for {city}: {e}"

    def _web_search(self, query: str) -> str:
        if not query:
            return "What would you like me to search for?"
        url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
        webbrowser.open(url)
        return f"Searching Google for: {query}"

    def _open_url(self, text: str) -> str:
        # Extract a URL-like token
        m = re.search(r"([\w\-]+\.[a-z]{2,}[\w/\-]*)", text)
        if m:
            url = m.group(1)
            if not url.startswith("http"):
                url = "https://" + url
            webbrowser.open(url)
            return f"Opening {url}"
        return "I couldn't find a website to open."

    def _wiki_summary(self, text: str) -> str:
        topic = re.sub(r"(who is|what is|tell me about)", "", text).strip()
        if not topic:
            return "What topic would you like to know about?"
        try:
            import urllib.request
            url = (f"https://en.wikipedia.org/api/rest_v1/page/summary/"
                   f"{topic.replace(' ', '_')}")
            req = urllib.request.Request(url,
                  headers={"User-Agent": "ARIA-Assistant/1.0"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode())
            return data.get("extract", "No summary available.")[:400] + "…"
        except Exception:
            return f"I couldn't find Wikipedia info on '{topic}'."

    def _calculate(self, text: str) -> str:
        expr = re.sub(r"[^0-9+\-*/().\s]", "", text).strip()
        if not expr:
            return "Please give me a math expression."
        try:
            result = eval(expr, {"__builtins__": {}})   # no builtins — safe
            return f"The answer is {result}"
        except Exception:
            return "I couldn't evaluate that expression."

    def _tell_joke(self) -> str:
        if REQUESTS_AVAILABLE:
            try:
                r = requests.get("https://official-joke-api.appspot.com/random_joke",
                                  timeout=4)
                j = r.json()
                return f"{j['setup']}  …  {j['punchline']}"
            except Exception:
                pass
        import random
        jokes = [
            "Why don't scientists trust atoms? Because they make up everything!",
            "I told my computer I needed a break. Now it won't stop sending me Kit-Kat ads.",
            "Why do programmers prefer dark mode? Because light attracts bugs.",
            "I would tell you a UDP joke, but you might not get it.",
        ]
        return random.choice(jokes)

    def _set_reminder(self, text: str) -> str:
        # Try to parse "remind me to X in Y minutes"
        m = re.search(r"(?:remind me to|reminder) (.+?) in (\d+) min", text)
        if m:
            task  = m.group(1).strip()
            mins  = int(m.group(2))
            due   = datetime.datetime.now() + datetime.timedelta(minutes=mins)
            CONFIG["reminders"].append({"text": task, "due": due})
            return f"Reminder set: '{task}' in {mins} minute(s)."
        return ("To set a reminder say: 'Remind me to [task] in [N] minutes'")

    def _list_reminders(self) -> str:
        if not CONFIG["reminders"]:
            return "You have no pending reminders."
        items = "\n".join(
            f"• {r['text']} at {r['due'].strftime('%H:%M')}"
            for r in CONFIG["reminders"]
        )
        return "Your reminders:\n" + items

    def _reminder_loop(self):
        """Background thread — fires reminders when due."""
        while True:
            now = datetime.datetime.now()
            due = [r for r in CONFIG["reminders"] if r["due"] <= now]
            for r in due:
                self.speak(f"Reminder: {r['text']}")
                CONFIG["reminders"].remove(r)
            time.sleep(20)

    def _send_email_prompt(self, text: str) -> str:
        if not CONFIG["email_sender"]:
            return ("Email is not configured. "
                    "Please set your email and app-password in Settings.")
        # Extract recipient if mentioned
        m = re.search(r"to ([a-zA-Z]+)", text)
        recipient_name = m.group(1) if m else None
        return f"EMAIL_PROMPT:{recipient_name or ''}"

    def send_email(self, to_addr: str, subject: str, body: str) -> str:
        try:
            msg = MIMEMultipart()
            msg["From"]    = CONFIG["email_sender"]
            msg["To"]      = to_addr
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain"))
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
                srv.login(CONFIG["email_sender"], CONFIG["email_password"])
                srv.sendmail(CONFIG["email_sender"], to_addr, msg.as_string())
            return f"Email sent to {to_addr}."
        except Exception as e:
            return f"Failed to send email: {e}"

    def _help_text(self) -> str:
        return (
            "I can help you with:\n"
            "• 'What time is it?' / 'What's today's date?'\n"
            "• 'Weather in [city]'\n"
            "• 'Search for [topic]'\n"
            "• 'What is / Who is [topic]'\n"
            "• 'Calculate [expression]'\n"
            "• 'Remind me to [task] in [N] minutes'\n"
            "• 'Show my reminders'\n"
            "• 'Send email'\n"
            "• 'Tell me a joke'\n"
            "• 'Open [website]'\n"
            "• 'Goodbye' to exit"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  GUI
# ══════════════════════════════════════════════════════════════════════════════
class AriaGUI(tk.Tk):
    # Palette
    BG      = "#080C14"
    PANEL   = "#0E1520"
    ACCENT  = "#00BFFF"      # electric blue
    ARIA_C  = "#00BFFF"
    USER_C  = "#F0A500"
    SYS_C   = "#606A80"
    TEXT    = "#C8D8EC"
    BORDER  = "#1C2840"
    GREEN   = "#00FF9D"
    RED     = "#FF4466"

    FONT_UI   = ("Consolas", 10)
    FONT_LOG  = ("Consolas", 11)
    FONT_HEAD = ("Consolas", 17, "bold")

    def __init__(self):
        super().__init__()
        self.title("ARIA — Voice Assistant")
        self.configure(bg=self.BG)
        self.resizable(True, True)
        self.minsize(680, 640)
        self._center(740, 700)

        self._listening = False
        self._text_queue: queue.Queue = queue.Queue()

        self._build_ui()
        self._engine = AriaEngine(
            log_cb    = self._enqueue_log,
            status_cb = self._set_status,
        )
        self._process_queue()

        self._greet()

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _center(self, w, h):
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    def _enqueue_log(self, text: str, tag: str):
        self._text_queue.put((text, tag))

    def _process_queue(self):
        while not self._text_queue.empty():
            text, tag = self._text_queue.get_nowait()
            self._append_log(text, tag)
        self.after(100, self._process_queue)

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # ── Top bar ──────────────────────────────────────────────────────────
        bar = tk.Frame(self, bg=self.PANEL, height=64)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        tk.Label(bar, text="▲ ARIA", font=self.FONT_HEAD,
                 bg=self.PANEL, fg=self.ACCENT).pack(side="left", padx=24, pady=16)
        tk.Label(bar, text="Advanced Recognition & Interaction Agent",
                 font=("Consolas", 9), bg=self.PANEL, fg=self.SYS_C).pack(
                     side="left", pady=(20, 0))

        self.status_dot = tk.Label(bar, text="●", font=("Consolas", 18),
                                    bg=self.PANEL, fg=self.GREEN)
        self.status_dot.pack(side="right", padx=(0, 8))
        self.status_lbl = tk.Label(bar, text="Idle",
                                    font=("Consolas", 9), bg=self.PANEL, fg=self.SYS_C)
        self.status_lbl.pack(side="right")

        tk.Button(bar, text="⚙ Settings", font=("Consolas", 9),
                  bg=self.PANEL, fg=self.SYS_C, relief="flat",
                  activebackground=self.BORDER, cursor="hand2",
                  command=self._open_settings).pack(side="right", padx=16)

        # Separator
        tk.Frame(self, bg=self.BORDER, height=1).pack(fill="x")

        # ── Log area ──────────────────────────────────────────────────────────
        log_frame = tk.Frame(self, bg=self.BG)
        log_frame.pack(fill="both", expand=True, padx=0, pady=0)

        self.log = scrolledtext.ScrolledText(
            log_frame,
            font=self.FONT_LOG,
            bg=self.BG, fg=self.TEXT,
            insertbackground=self.ACCENT,
            relief="flat", bd=0,
            wrap="word",
            state="disabled",
            padx=20, pady=16,
        )
        self.log.pack(fill="both", expand=True)
        self.log.tag_config("aria", foreground=self.ARIA_C)
        self.log.tag_config("user", foreground=self.USER_C)
        self.log.tag_config("sys",  foreground=self.SYS_C)
        self.log.tag_config("help", foreground=self.TEXT)

        # ── Bottom controls ───────────────────────────────────────────────────
        tk.Frame(self, bg=self.BORDER, height=1).pack(fill="x")

        ctrl = tk.Frame(self, bg=self.PANEL, height=80)
        ctrl.pack(fill="x")
        ctrl.pack_propagate(False)

        # Text input
        self.text_var = tk.StringVar()
        self.text_entry = tk.Entry(
            ctrl, textvariable=self.text_var,
            font=self.FONT_LOG, bg=self.BG, fg=self.TEXT,
            insertbackground=self.ACCENT, relief="flat",
            highlightthickness=1, highlightbackground=self.BORDER,
            highlightcolor=self.ACCENT,
        )
        self.text_entry.pack(side="left", fill="x", expand=True,
                              padx=(20, 8), pady=20, ipady=8)
        self.text_entry.bind("<Return>", lambda _: self._submit_text())

        # Send button
        tk.Button(ctrl, text="↵ SEND",
                  font=("Consolas", 10, "bold"),
                  bg=self.ACCENT, fg=self.BG, relief="flat",
                  activebackground="#009ACC", cursor="hand2",
                  padx=14, pady=6,
                  command=self._submit_text).pack(side="left", padx=(0, 8), pady=20)

        # Mic button
        self.mic_btn = tk.Button(ctrl, text="🎙 SPEAK",
                                  font=("Consolas", 10, "bold"),
                                  bg=self.GREEN, fg=self.BG, relief="flat",
                                  activebackground="#00CC7A", cursor="hand2",
                                  padx=14, pady=6,
                                  command=self._toggle_listen)
        self.mic_btn.pack(side="left", padx=(0, 20), pady=20)

        # Mic unavailable hint
        if not SR_AVAILABLE:
            self.mic_btn.config(state="disabled", text="🎙 (install SR)")

    # ── Interaction ───────────────────────────────────────────────────────────
    def _greet(self):
        self._engine.speak("Hello! I'm ARIA. How can I assist you today?")

    def _submit_text(self):
        text = self.text_var.get().strip()
        if not text:
            return
        self.text_var.set("")
        self._enqueue_log(f"YOU ▸ {text}", "user")
        self._run_in_thread(self._handle_input, text)

    def _toggle_listen(self):
        if self._listening:
            return
        self._run_in_thread(self._listen_and_handle)

    def _listen_and_handle(self):
        self._listening = True
        self.mic_btn.config(bg=self.RED, text="● LISTENING")
        text = self._engine.listen_once()
        self.mic_btn.config(bg=self.GREEN, text="🎙 SPEAK")
        self._listening = False
        if text:
            self._handle_input(text)

    def _handle_input(self, text: str):
        response = self._engine.process(text)

        if response == "STOP":
            self._engine.speak("Goodbye! Have a wonderful day.")
            self.after(1200, self.destroy)
            return

        if response.startswith("EMAIL_PROMPT:"):
            self.after(0, self._email_dialog)
            return

        self._engine.speak(response)

    def _run_in_thread(self, fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    # ── Logging ───────────────────────────────────────────────────────────────
    def _append_log(self, text: str, tag: str):
        self.log.config(state="normal")
        ts = datetime.datetime.now().strftime("%H:%M")
        self.log.insert("end", f"[{ts}]  {text}\n\n", tag)
        self.log.see("end")
        self.log.config(state="disabled")

    def _set_status(self, text: str):
        colour = self.RED if "listen" in text.lower() else \
                 self.USER_C if "recogn" in text.lower() else self.GREEN
        self.status_lbl.config(text=text)
        self.status_dot.config(fg=colour)

    # ── Settings ──────────────────────────────────────────────────────────────
    def _open_settings(self):
        win = tk.Toplevel(self)
        win.title("Settings")
        win.configure(bg=self.BG)
        win.resizable(False, False)
        win.geometry("480x520")

        tk.Label(win, text="⚙  SETTINGS", font=("Consolas", 13, "bold"),
                 bg=self.BG, fg=self.ACCENT).pack(padx=20, pady=(20, 14), anchor="w")

        fields = [
            ("Wake Word",          "wake_word"),
            ("Default City",       "weather_city"),
            ("Voice Rate (WPM)",   "voice_rate"),
            ("Email (Gmail)",      "email_sender"),
            ("Email App Password", "email_password"),
        ]
        entries: dict[str, tk.StringVar] = {}

        for label, key in fields:
            row = tk.Frame(win, bg=self.BG)
            row.pack(fill="x", padx=20, pady=4)
            tk.Label(row, text=label, font=self.FONT_UI, width=22,
                     bg=self.BG, fg=self.SYS_C, anchor="w").pack(side="left")
            var = tk.StringVar(value=str(CONFIG[key]))
            entries[key] = var
            show = "*" if "password" in key.lower() else ""
            tk.Entry(row, textvariable=var, font=self.FONT_UI,
                     bg=self.PANEL, fg=self.TEXT, insertbackground=self.ACCENT,
                     relief="flat", show=show,
                     highlightthickness=1, highlightbackground=self.BORDER).pack(
                         side="left", fill="x", expand=True, ipady=5)

        # Custom commands
        tk.Label(win, text="Custom Commands  (phrase=response, one per line)",
                 font=("Consolas", 8), bg=self.BG, fg=self.SYS_C).pack(
                     padx=20, pady=(14, 2), anchor="w")
        custom_box = tk.Text(win, height=6, font=self.FONT_UI,
                              bg=self.PANEL, fg=self.TEXT, relief="flat",
                              insertbackground=self.ACCENT,
                              highlightthickness=1, highlightbackground=self.BORDER)
        custom_box.pack(fill="x", padx=20, pady=(0, 14))
        for k, v in CONFIG["custom_commands"].items():
            custom_box.insert("end", f"{k}={v}\n")

        def save():
            for key, var in entries.items():
                val = var.get().strip()
                CONFIG[key] = int(val) if key == "voice_rate" else val
            # Parse custom commands
            raw = custom_box.get("1.0", "end").strip().splitlines()
            CONFIG["custom_commands"] = {}
            for line in raw:
                if "=" in line:
                    k, _, v = line.partition("=")
                    CONFIG["custom_commands"][k.strip()] = v.strip()
            self._engine._init_tts()   # re-initialise TTS with new rate
            win.destroy()
            messagebox.showinfo("Settings", "Settings saved!")

        tk.Button(win, text="SAVE SETTINGS",
                  font=("Consolas", 10, "bold"),
                  bg=self.ACCENT, fg=self.BG, relief="flat",
                  padx=16, pady=8, cursor="hand2",
                  command=save).pack(pady=(4, 20))

    # ── Email dialog ──────────────────────────────────────────────────────────
    def _email_dialog(self):
        win = tk.Toplevel(self)
        win.title("Send Email")
        win.configure(bg=self.BG)
        win.resizable(False, False)
        win.geometry("420x360")

        tk.Label(win, text="✉  SEND EMAIL", font=("Consolas", 13, "bold"),
                 bg=self.BG, fg=self.ACCENT).pack(padx=20, pady=(18, 12), anchor="w")

        def field(label):
            f = tk.Frame(win, bg=self.BG); f.pack(fill="x", padx=20, pady=4)
            tk.Label(f, text=label, font=self.FONT_UI, width=10,
                     bg=self.BG, fg=self.SYS_C, anchor="w").pack(side="left")
            var = tk.StringVar()
            tk.Entry(f, textvariable=var, font=self.FONT_UI,
                     bg=self.PANEL, fg=self.TEXT, insertbackground=self.ACCENT,
                     relief="flat", highlightthickness=1,
                     highlightbackground=self.BORDER).pack(
                         side="left", fill="x", expand=True, ipady=5)
            return var

        to_var      = field("To")
        subject_var = field("Subject")

        tk.Label(win, text="Body", font=self.FONT_UI,
                 bg=self.BG, fg=self.SYS_C).pack(padx=20, anchor="w", pady=(8, 2))
        body_box = tk.Text(win, height=5, font=self.FONT_UI,
                            bg=self.PANEL, fg=self.TEXT, relief="flat",
                            insertbackground=self.ACCENT,
                            highlightthickness=1, highlightbackground=self.BORDER)
        body_box.pack(fill="x", padx=20, pady=(0, 14))

        def send():
            resp = self._engine.send_email(
                to_var.get(), subject_var.get(),
                body_box.get("1.0", "end"))
            self._engine.speak(resp)
            win.destroy()

        tk.Button(win, text="SEND ↗",
                  font=("Consolas", 10, "bold"),
                  bg=self.ACCENT, fg=self.BG, relief="flat",
                  padx=16, pady=8, cursor="hand2",
                  command=send).pack(pady=4)


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = AriaGUI()
    app.mainloop()
