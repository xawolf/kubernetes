from fastapi import FastAPI, Request, HTTPException
import requests
import redis
import os
import uuid
import json
import uvicorn
from prometheus_fastapi_instrumentator import Instrumentator
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI()

# Initialize Prometheus Instrumentator
Instrumentator().instrument(app).expose(app)

# Redis configuration
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
REDIS_DB = int(os.getenv('REDIS_DB', 0))
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB)

# Keycloak configuration
KEYCLOAK_URL = os.getenv('KEYCLOAK_URL', 'https://prd-keycloak.si24.ir')
KEYCLOAK_CLIENT_ID = os.getenv('KEYCLOAK_CLIENT_ID')
KEYCLOAK_CLIENT_SECRET = os.getenv('KEYCLOAK_CLIENT_SECRET')
KEYCLOAK_CLIENT_USERNAME = os.getenv('KEYCLOAK_CLIENT_USERNAME')
KEYCLOAK_CLIENT_PASSWORD = os.getenv('KEYCLOAK_CLIENT_PASSWORD')

# SMS Provider configuration
SMS_API_URL = os.getenv('SMS_API_URL', 'https://esb.si24.ir:8243/notification/v1/api/v1/SMS')

# Load contacts from JSON file
CONTACTS_FILE = os.getenv('CONTACTS_FILE', 'contacts.json')
with open(CONTACTS_FILE) as f:
    contacts = json.load(f)

def get_oidc_token():
    logger.debug("Attempting to retrieve OIDC token from Redis.")
    token = redis_client.get('oidc_token')
    if token:
        logger.info("OIDC token retrieved from Redis.")
        return token.decode('utf-8')
    
    logger.info("OIDC token not found in Redis, requesting new token.")
    response = requests.post(
        f"{KEYCLOAK_URL}/realms/notif-sms/protocol/openid-connect/token",
        data={
            'client_id': KEYCLOAK_CLIENT_ID,
            'client_secret': KEYCLOAK_CLIENT_SECRET,
            'grant_type': 'client_credentials',
            'username': KEYCLOAK_CLIENT_USERNAME,
            'password': KEYCLOAK_CLIENT_PASSWORD
        })
    
    if response.status_code != 200:
        logger.error(f"Failed to obtain OIDC token. Status code: {response.status_code}")
        raise HTTPException(status_code=500, detail="Failed to obtain OIDC token")

    token_data = response.json()
    token = token_data['access_token']
    expires_in = 120
    
    redis_client.set('oidc_token', token, ex=expires_in)
    logger.info("New OIDC token stored in Redis.")
    
    return token

def send_sms(phone: str, message: str, oidc_token: str):
    unique_id = str(uuid.uuid4())
    headers = {
        'Authorization': f'Bearer {oidc_token}',
        'Content-Type': 'application/json',
        'request-id': unique_id
    }
    payload = {
        'phoneNumber': phone,
        'body': message,
        'type': 'Information'
    }
    
    logger.debug(f"Sending SMS to {phone}. Message: {message}")
    response = requests.post(SMS_API_URL, json=payload, headers=headers, verify=False)
    
    if response.status_code != 200:
        logger.error(f"Failed to send SMS. Status code: {response.status_code}")
        raise HTTPException(status_code=500, detail="Failed to send SMS")
    logger.info(f"SMS sent successfully to {phone}.")

@app.post("/alert")
async def alert(request: Request):
    data = await request.json()
    alerts = data.get('alerts', [])
    
    for alert in alerts:
        message = f"Alert: {alert.get('annotations', {}).get('description', 'No description provided')}"
        team = alert.get('labels', {}).get('team')
        oidc_token = get_oidc_token()
        
        if not team or team not in contacts:
            logger.info(f"missing team information in alert: {alert}, sending to default team")

            team = 'devops'
            for member in contacts[team]:
                send_sms(member['phone'], message, oidc_token)
        

            send_sms(member['phone'], message, oidc_token)
    
    logger.info("Alerts processed successfully.")
    return {"status": "Alerts processed"}

# To run the app, use: uvicorn main:app --reload
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
