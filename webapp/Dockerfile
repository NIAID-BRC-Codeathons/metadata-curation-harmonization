FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py database.py ncbi_import.py ncbi_download.py relationships.py graph_views.py report_store.py report_views.py ./
COPY templates ./templates
COPY static ./static
COPY examples ./examples
RUN useradd --create-home explorer && mkdir /data && chown explorer:explorer /data
USER explorer
ENV EXPLORER_DB=/data/explorer.sqlite3
EXPOSE 8000
CMD ["python", "app.py", "serve", "--host", "0.0.0.0"]
