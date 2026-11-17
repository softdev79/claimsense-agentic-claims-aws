# ClaimSense - Agentic Motor Insurance Claims Processor | Author: Vishal Gupta (AWS capstone edition), based on ClaimSense by Shivank Agrawal
# One image, two processes: the API (default command) and the Streamlit portal (command override).
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# Install dependencies first so Docker caches this layer when only code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# Build the sample database + claim PDFs inside the image (no API key needed for this step)
RUN python -m scripts.generate_data

EXPOSE 8000 8501
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
