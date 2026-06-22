import os
import json
import math
from datetime import datetime
import psycopg2  # Changed from sqlite3 to psycopg2
import streamlit as st
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from typing import List

# ----------------------------------------------------
# HARDCODED CONFIGURATION & ENVIRONMENT DB SECRETS
# ----------------------------------------------------
# Read Connection String from environment variables provided by your hosting provider
# e.g., postgres://user:password@hostname:5432/dbname
DATABASE_URL = os.environ.get("DATABASE_URL")

# ----------------------------------------------------
# 1. DATABASE SETUP (POSTGRESQL SYNTAX)
# ----------------------------------------------------
def get_db_connection():
    """Returns a connected transaction instance to the PostgreSQL database cluster."""
    if not DATABASE_URL:
        st.error("Missing DB environment setup configuration. Please define DATABASE_URL in your management panel secrets.")
        st.stop()
    # SSL mode is required by modern serverless databases like Neon or Supabase
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    """Creates a production PostgreSQL database table using native data structures."""
    conn = get_db_connection()
    cursor = conn.cursor()
    # PostgreSQL uses VARCHAR/TEXT and natively typed TIMESTAMP declarations
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cache (
            word TEXT PRIMARY KEY,
            data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    cursor.close()
    conn.close()

def get_cached_word(word: str):
    """Checks the live cloud database cluster for matching entries."""
    conn = get_db_connection()
    cursor = conn.cursor()
    # PostgreSQL placeholder syntax changes from local '?' to '%s'
    cursor.execute("SELECT data FROM cache WHERE word = %s", (word.strip().lower(),))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row[0] if row else None

def save_word_to_cache(word: str, json_data: str):
    """Inserts or overwrites an active vocabulary tracking entry into Cloud PostgreSQL."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # PostgreSQL does not use "INSERT OR REPLACE". Instead, it utilizes standard ANSI "ON CONFLICT" UPSERT statements.
    cursor.execute('''
        INSERT INTO cache (word, data, created_at) 
        VALUES (%s, %s, %s)
        ON CONFLICT (word) 
        DO UPDATE SET data = EXCLUDED.data, created_at = EXCLUDED.created_at
    ''', (word.strip().lower(), json_data, now))
    
    conn.commit()
    cursor.close()
    conn.close()

def delete_word_from_cache(word: str):
    """Permanently drops the specified record row instance from the cloud table tracking."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM cache WHERE word = %s", (word.strip().lower(),))
    conn.commit()
    cursor.close()
    conn.close()

def fetch_filtered_cached_words(search_query="", sort_by="Alphabetical"):
    """Fetches text matches sequentially from the external live cluster."""
    conn = get_db_connection()
    cursor = conn.cursor()
    base_query = "SELECT word, data FROM cache WHERE word LIKE %s"
    param = f"%{search_query.strip().lower()}%"
    
    if sort_by == "Alphabetical":
        query = f"{base_query} ORDER BY word ASC"
    elif sort_by == "Newest First":
        query = f"{base_query} ORDER BY created_at DESC"
    elif sort_by == "Oldest First":
        query = f"{base_query} ORDER BY created_at ASC"
        
    cursor.execute(query, (param,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

# Run the schema validation check on cluster setup initialization
init_db()

# ----------------------------------------------------
# 2. DATA SCHEMA DEFINITIONS
# ----------------------------------------------------
class MeaningSense(BaseModel):
    meaning_english: str = Field(description="Clear definition of this specific meaning in English")
    meaning_marathi: str = Field(description="Accurate translation of this specific meaning in Marathi")
    example_eng: str = Field(description="An illustrative example sentence using the word with THIS specific meaning")
    example_mar: str = Field(description="The Marathi translation of this specific example sentence")

class DictionaryEntry(BaseModel):
    word: str = Field(description="The English word")
    pronunciation_marathi: str = Field(description="The pronunciation written in Marathi script")
    part_of_speech: str = Field(description="Noun, Verb, Adjective, Adverb, etc.")
    opposite: str = Field(description="An antonym/opposite word in English")
    meanings: List[MeaningSense] = Field(description="List of different meanings with dedicated examples.")


# ----------------------------------------------------
# 3. MAIN WEB APPLICATION SETUP
# ----------------------------------------------------
st.set_page_config(page_title="AI Smart Dictionary", page_icon="📖", layout="centered")

st.title("📖 AI Dictionary & Word Repository")

api_key = os.environ.get("GEMINI_API_KEY") or st.sidebar.text_input("Enter Gemini API Key", type="password")

# --- SECTION 1: SEARCH & ADD NEW WORD ---
st.markdown("### ➕ Add New Word")
target_word = st.text_input("Enter a new English word:", placeholder="e.g., balance, critical, strike", key="new_word_input")

if st.button("Generate & Save", type="primary") and target_word:
    search_term = target_word.strip().lower()
    
    if not api_key:
        st.error("Please provide a Gemini API key to generate new words.")
    else:
        cached_json = get_cached_word(search_term)
        if cached_json:
            st.warning(f"'{target_word}' already exists in your dictionary repository below!")
        else:
            client = genai.Client(api_key=api_key)
            with st.spinner(f"AI is researching all meanings for '{target_word}'..."):
                try:
                    response = client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=(
                            f"Generate a dictionary entry for the English word: '{target_word}'. "
                            f"If the word has multiple common definitions or uses, list each distinct meaning "
                            f"separately and provide an accurate context-specific example for each."
                        ),
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=DictionaryEntry,
                            temperature=0.3
                        ),
                    )
                    save_word_to_cache(search_term, response.text)
                    st.success(f"Added '{target_word}' successfully!")
                    st.rerun() 
                except Exception as e:
                    st.error(f"Error calling AI: {e}")

st.markdown("---")

# --- SECTION 2: SORTED, PAGINATED & EXPANDABLE LIST WITH SEARCH ---
st.markdown("### 🗂️ Stored Dictionary Words")

search_query = st.text_input("🔍 Search existing words in your database:", placeholder="Type a word to filter immediately...")

col1, col2 = st.columns([2, 1])
with col1:
    sort_choice = st.selectbox("Sort words by:", ["Alphabetical", "Newest First", "Oldest First"])
with col2:
    items_per_page = st.selectbox("Words per page:", [5, 10, 20, 50], index=1)

all_words = fetch_filtered_cached_words(search_query=search_query, sort_by=sort_choice)
total_words = len(all_words)

if total_words == 0:
    if search_query:
        st.info(f"No cached words match your search query: '{search_query}'")
    else:
        st.info("Your dictionary database is currently empty. Add a word above to see it appear here!")
else:
    total_pages = math.ceil(total_words / items_per_page)
    current_page = st.number_input("Page Selector", min_value=1, max_value=max(1, total_pages), step=1, value=1, label_visibility="collapsed")
    
    start_idx = (current_page - 1) * items_per_page
    end_idx = start_idx + items_per_page
    page_words = all_words[start_idx:end_idx]

    st.caption(f"Showing words {start_idx + 1} - {min(end_idx, total_words)} of **{total_words}** matching items")

    for word_name, raw_json in page_words:
        try:
            entry = DictionaryEntry.model_validate_json(raw_json)
            accordion_title = f"🔤 {entry.word.capitalize()} ({entry.part_of_speech})"
            
            with st.expander(accordion_title):
                st.markdown(f"**Pronunciation:** <span style='color:#d63384;'>{entry.pronunciation_marathi}</span>", unsafe_allow_html=True)
                st.markdown(f"**Opposite:** {entry.opposite}")
                st.markdown("<hr style='margin:10px 0;'>", unsafe_allow_html=True)
                
                for i, sense in enumerate(entry.meanings, start=1):
                    st.markdown(f"**Meaning {i}:**")
                    st.write(f"💡 {sense.meaning_english} | **{sense.meaning_marathi}**")
                    st.info(f"**Example:** {sense.example_eng}\n\n**मराठी:** {sense.example_mar}")
                    if i < len(entry.meanings):
                        st.markdown("---")
                
                st.markdown("<br>", unsafe_allow_html=True)
                
                if st.button(f"🗑️ Delete '{entry.word.capitalize()}'", key=f"del_{word_name}"):
                    delete_word_from_cache(word_name)
                    st.success(f"Deleted '{entry.word.capitalize()}' permanently!")
                    st.rerun() 
                    
        except Exception as e:
            st.error(f"Could not correctly read structure for word '{word_name}': {e}")