# Findr: Campus Lost & Found Portal
Run: `pip install -r requirements.txt` then `python app.py`. The browser opens at http://127.0.0.1:5055 by itself.
Moderator: nav link "Moderator" (set `ADMIN_PASSWORD`; demo default `admin123`). Also set `SECRET_KEY`. Delete `lostfound.db` to reset.
Features: categorized posts, filters (type, category, place, date), sort, photo upload, private claim workflow, AI match suggestions,
moderator queue and reported-post review, search alerts, safe hand-in points, 60-day expiry, reunited counter.
Safe contact: contact details are never stored or shown. A claimant answers a private check question; the poster approves via a private link;
only then does the claimant see the poster's chosen hand-over spot.
AI: TF-IDF cosine + concept map over title, description, category, place. Suggestions only; people confirm or dismiss.
