FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=3000 \
    DATA_DIR=/mnt/nvme/Teaching/docker-development \
    TMPDIR=/mnt/nvme/Teaching/docker-development/tmp/web

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends bubblewrap texlive-latex-base texlive-latex-recommended \
    texlive-pictures texlive-latex-extra \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 faculty \
    && useradd --uid 10001 --gid faculty --no-create-home faculty \
    && mkdir -p "$DATA_DIR" && chown faculty:faculty "$DATA_DIR"
# Keep every first-party runtime module available to app.create_app and workers.
# The former hand-picked root files predated the grading and assistant services.
COPY *.py ./
COPY assignment_template_migrations ./assignment_template_migrations
COPY calendar_migrations ./calendar_migrations
COPY absences ./absences
COPY assistant ./assistant
COPY bubble_sheets ./bubble_sheets
COPY canvas ./canvas
COPY communications ./communications
COPY course_notes ./course_notes
COPY decisions_sandbox ./decisions_sandbox
COPY dr_quiz_takers ./dr_quiz_takers
COPY exam_day ./exam_day
COPY exam_workflow ./exam_workflow
COPY faculty_embedding ./faculty_embedding
COPY file_viewer ./file_viewer
COPY gmail_mail ./gmail_mail
COPY google_calendar ./google_calendar
COPY gradebook ./gradebook
COPY grading ./grading
COPY iclicker ./iclicker
COPY jev ./jev
COPY memory_sandbox ./memory_sandbox
COPY picture_search ./picture_search
COPY question_bank ./question_bank
COPY room_pdf_sources ./room_pdf_sources
COPY scheduling ./scheduling
COPY student_notes ./student_notes
COPY student_photos ./student_photos
COPY student_support ./student_support
COPY teaching_assistants ./teaching_assistants
COPY teach_inbox ./teach_inbox
COPY document_identity ./document_identity
COPY faculty_ocr ./faculty_ocr
COPY response_cards ./response_cards
COPY static ./static
USER faculty
ENTRYPOINT ["sh", "-c", "umask 077; mkdir -p \"$TMPDIR\" && exec \"$@\"", "faculty"]
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3000/api/health', timeout=3)"
CMD ["python", "app.py"]
