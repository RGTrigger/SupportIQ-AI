from __future__ import annotations

import streamlit as st

from supportiq.config import get_settings
from supportiq.db import init_db, seed_demo_data
from supportiq.ui import render_app
from supportiq.rag import sync_vector_index


st.set_page_config(page_title="SupportIQ AI", page_icon="◈", layout="wide", initial_sidebar_state="expanded")
settings = get_settings()
init_db(settings.db_path)
seed_demo_data(settings.db_path)
sync_vector_index(settings.db_path, settings.vector_path)
render_app(settings)
