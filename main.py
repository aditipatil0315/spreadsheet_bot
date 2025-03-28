from fastapi import FastAPI, HTTPException, status, Depends, Body
from fastapi.middleware.cors import CORSMiddleware # Import CORSMiddleware
from pydantic import BaseModel, Field
from google.oauth2.service_account import Credentials
import gspread
from openai import OpenAI
from typing import Optional
from google.auth.transport.requests import Request
import json

app = FastAPI()

# --- CORS Middleware Configuration ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins (for development).  In production, specify your frontend's origin.
    allow_credentials=True,
    allow_methods=["*"],  # Allows all HTTP methods (GET, POST, etc.)
    allow_headers=["*"],  # Allows all headers
)

# --- Google Sheets Configuration ---
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
SERVICE_ACCOUNT_FILE = 'credentials.json'  # Ensure this path is correct

def get_google_sheets_client():
    """
    Authenticates with Google Sheets and returns a client.
    """
    try:
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"Error authenticating with Google Sheets: {e}")
        raise  # Re-raise to be caught by FastAPI's exception handling

def get_sheet(client: gspread.Client):
    """
    Opens the specified Google Sheet.  Handles the case where the sheet doesn't exist.

    Args:
        client: The authorized gspread client.

    Returns:
        The opened worksheet.  Raises HTTPException if the sheet is not found.
    """
    try:
        return client.open('Untitled spreadsheet').sheet1 # changed to a function parameter
    except gspread.SpreadsheetNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Spreadsheet not found.  Please ensure the spreadsheet exists and the name is correct.",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error opening spreadsheet: {e}",
        )

# --- OpenAI Configuration ---
OPENAI_API_KEY = "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" # Ideally, load this from an environment variable
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# --- Data Models ---

class FinanceAction(BaseModel):
    action: str = Field(..., description="Action to perform: 'update', 'read', 'monthly_update', 'monthly_read', 'monthly_payment'")
    name: Optional[str] = Field(None, description="Name of the person involved")
    change: Optional[float] = Field(None, description="Amount of money received (positive) or paid (negative)")
    monthly_amount: Optional[float] = Field(None, description="Monthly payment amount")
    months: Optional[int] = Field(1, description="Number of months for monthly payment")
class MessageInput(BaseModel):
    message: str = Field(..., description="The message from the user")

# --- Helper Functions ---
SYSTEM_PROMPT = """
You are an AI that extracts financial transactions from user messages.
Your task is to analyze the user's message and return a JSON object with:
- "action": "update", "read", "monthly_update", "monthly_read", "monthly_payment"
- "name": The name of the person involved
- "change": The amount of money received (positive) or paid (negative)
- "monthly_amount": Monthly payment to be set (for monthly_update)
- "months": (optional) number of months (for monthly_update)

Examples:
- "Manthan just paid me 1000rs" -> {"action": "update", "name": "Manthan", "change": -1000}
- "I received 500 from Rahul" -> {"action": "update", "name": "Rahul", "change": -500} 
- "What's the balance?" -> {"action": "read"}
- "Set Rahul's monthly payment to 1500" -> {"action": "monthly_update", "name": "Rahul", "monthly_amount": 1500}
- "Set monthly payment for 500 for 5 months for Rahul" -> {"action": "monthly_update", "name": "Rahul", "monthly_amount": 500, "months": 5}
- "Bob bought items worth 5000" -> {"action": "update", "name": "Bob", "change": 5000}
- "What's Manthan's monthly payment?" -> {"action": "monthly_read", "name": "Manthan"}
- "What's Manthan's balance?" -> {"action": "read", "name": "Manthan"}
- "How much does Rahul owe me?" -> {"action": "read", "name": "Rahul"}
- "Manthan just paid me this month's due" -> {"action": "monthly_payment", "name": "Manthan"}

# Additional examples:
- "Riya sent me 2000 for the rent" -> {"action": "update", "name": "Riya", "change": -2000}
- "Paid 300 to Ankit" -> {"action": "update", "name": "Ankit", "change": 300}
- "Nina gave me 400 yesterday" -> {"action": "update", "name": "Nina", "change": -400}
- "Sahil transferred 750 today" -> {"action": "update", "name": "Sahil", "change": -750}
- "Bought groceries worth 600 for Jenny" -> {"action": "update", "name": "Jenny", "change": 600}
- "Set Mehul's monthly payment as 1000" -> {"action": "monthly_update", "name": "Mehul", "monthly_amount": 1000}
- "Set monthly of 800 for 3 months for Sonal" -> {"action": "monthly_update", "name": "Sonal", "monthly_amount": 800, "months": 3}
- "What’s the total Arjun owes me?" -> {"action": "read", "name": "Arjun"}
- "How much is Sneha's monthly due?" -> {"action": "monthly_read", "name": "Sneha"}
- "Dinesh paid this month’s EMI" -> {"action": "monthly_payment", "name": "Dinesh"}
- "Pranav cleared his dues for this month" -> {"action": "monthly_payment", "name": "Pranav"}
- "How much money do I have to take from Neha?" -> {"action": "read", "name": "Neha"}
- "Divya gave me 1200 for the bill" -> {"action": "update", "name": "Divya", "change": -1200}
- "I paid for Ayush's Uber ride – 250" -> {"action": "update", "name": "Ayush", "change": 250}
- "Karan paid me 1500 as advance" -> {"action": "update", "name": "Karan", "change": -1500}
"""

def get_ai_response(message: str) -> FinanceAction:
    """
    Sends the user message to OpenAI and returns the parsed response.
    """
    try:
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": message}
            ]
        )
        content_raw = response.choices[0].message.content.strip()
        result = json.loads(content_raw)
        return FinanceAction(**result)  # Validate the structure
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON response from OpenAI: {e}.  Raw response: {content_raw}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error communicating with OpenAI: {e}",
        )
    
def read_balance(sheet: gspread.Worksheet):
    """Reads all data from the sheet."""
    try:
        data = sheet.get_all_values()
        return data  # Returns the raw data
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error reading data from sheet: {e}",
        )

def read_data(name: str, sheet: gspread.Worksheet):
    """Reads a specific person's balance from the sheet."""
    try:
        data = sheet.get_all_values()
        for row in data:
            if row[0].lower() == name.lower():
                pending = float(row[1]) if row[1] else 0
                return {"name": name, "pending": pending}
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No record found for {name}.",
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid pending value in sheet.",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error reading data from sheet: {e}",
        )

def update_sheet_data(name: str, change: float, sheet: gspread.Worksheet):
    """Updates a person's balance in the sheet."""
    try:
        data = sheet.get_all_values()
        for i, row in enumerate(data):
            if row[0].lower() == name.lower():
                pending = float(row[1]) if row[1] else 0
                updated = pending + change
                sheet.update_cell(i + 1, 2, updated)
                return {"name": name, "updated_balance": updated}
        # If name not found, append a new row
        sheet.append_row([name, change, '', '', ''])
        return {"name": name, "new_balance": change, "record_created": True}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating sheet: {e}",
        )
    
def update_monthly_payment(name: str, amount: float, months: int, sheet: gspread.Worksheet):
    """Updates monthly payment details for a person."""
    try:
        data = sheet.get_all_values()
        for i, row in enumerate(data):
            if row[0].lower() == name.lower():
                total_months = int(months)
                sheet.update_cell(i + 1, 3, amount)  # Monthly Payment
                sheet.update_cell(i + 1, 4, total_months)  # Total Months
                sheet.update_cell(i + 1, 5, 0)  # Paid Months
                pending_amount = amount * total_months
                sheet.update_cell(i+1, 2, pending_amount)
                return {"name": name, "monthly_payment": amount, "total_months": total_months}
        new_pending = amount * months
        sheet.append_row([name, new_pending, amount, months, '0'])
        return {"name": name, "monthly_payment": amount, "total_months": months, "record_created": True}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating monthly payment: {e}",
        )

def read_monthly_payment(name: str, sheet: gspread.Worksheet):
    """Reads monthly payment details for a person."""
    try:
        data = sheet.get_all_values()
        for row in data:
            if row[0].lower() == name.lower():
                return {
                    "name": name,
                    "monthly_payment": row[2],
                    "total_months": row[3],
                    "paid_months": row[4],
                }
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No record found for {name}.",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error reading monthly payment: {e}",
        )

def process_monthly_payment(name: str, sheet: gspread.Worksheet):
    """Processes a monthly payment for a person."""
    try:
        data = sheet.get_all_values()
        for i, row in enumerate(data):
            if row[0].lower() == name.lower():
                # Get current values
                monthly_amount = float(row[2]) if row[2] else 0
                total_months = int(row[3]) if row[3] else 0
                paid_months = int(row[4]) if row[4] else 0
                current_pending = float(row[1]) if row[1] else 0
                
                # Check if payment is possible
                if paid_months >= total_months:
                    return {
                        "name": name,
                        "status": "error",
                        "message": "All monthly payments are already marked as paid"
                    }
                
                # Update paid months
                new_paid_months = paid_months + 1
                sheet.update_cell(i + 1, 5, new_paid_months)
                
                # Update pending amount
                new_pending = current_pending - monthly_amount
                sheet.update_cell(i + 1, 2, new_pending)
                
                return {
                    "name": name,
                    "status": "success",
                    "monthly_amount": monthly_amount,
                    "paid_months": new_paid_months,
                    "total_months": total_months,
                    "remaining_months": total_months - new_paid_months,
                    "new_pending": new_pending
                }
                
        # If name not found
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No monthly payment record found for {name}."
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing monthly payment: {e}"
        )

# --- API Endpoints ---

@app.post("/process_finance_message/")
async def process_finance_message(message_input: MessageInput, client: gspread.Client = Depends(get_google_sheets_client)):
    """
    Processes a finance-related message using OpenAI and updates/reads data in Google Sheets.
    """
    try:
        sheet = get_sheet(client) # Get the sheet.
        action_data = get_ai_response(message_input.message) # Access the message from the model

        action = action_data.action
        name = action_data.name

        if action == "read":
            if name:
                return read_data(name, sheet)
            else:
                return read_balance(sheet)
        elif action == "update":
            change = action_data.change
            if name is None or change is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Name and change are required for update action.",
                )
            return update_sheet_data(name, change, sheet)
        elif action == "monthly_update":
            monthly_amount = action_data.monthly_amount
            months = action_data.months
            if name is None or monthly_amount is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Name and monthly_amount are required for monthly_update action.",
                )
            return update_monthly_payment(name, monthly_amount, months, sheet)
        elif action == "monthly_read":
            if name is None:
                 raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Name  is required for monthly_read action.",
                )
            return read_monthly_payment(name, sheet)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid action: {action}",
            )
    except HTTPException as e:
        raise e # Explicitly re-raise HTTPExceptions
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {e}",
        )
@app.get("/dashboard/")
async def get_dashboard_data(client: gspread.Client = Depends(get_google_sheets_client)):
    """
    Returns aggregated financial data for dashboard visualization.
    
    The response includes:
    - A list of all people with their balances and payment details
    - Total pending amount across all people
    - Monthly payment statistics
    """
    try:
        sheet = get_sheet(client)
        data = sheet.get_all_values()
        
        # Skip header row if it exists
        if data and len(data) > 0 and data[0][0].lower() in ["name", "person", "user"]:
            data = data[1:]
        
        dashboard_data = {
            "people": [],
            "total_pending": 0,
            "total_monthly_commitments": 0,
            "payment_progress": {
                "completed": 0,
                "in_progress": 0,
            }
        }
        
        for row in data:
            if len(row) >= 5:  # Ensure row has all required columns
                name = row[0]
                pending = float(row[1]) if row[1] and row[1].strip() else 0
                monthly_amount = float(row[2]) if row[2] and row[2].strip() else 0
                total_months = int(row[3]) if row[3] and row[3].strip() else 0
                paid_months = int(row[4]) if row[4] and row[4].strip() else 0
                
                person_data = {
                    "name": name,
                    "pending_amount": pending,
                    "monthly_payment": monthly_amount,
                    "total_months": total_months,
                    "paid_months": paid_months,
                    "remaining_months": max(0, total_months - paid_months),
                    "payment_progress_percentage": (paid_months / total_months * 100) if total_months > 0 else 0
                }
                
                dashboard_data["people"].append(person_data)
                dashboard_data["total_pending"] += pending
                
                if monthly_amount > 0 and total_months > 0:
                    dashboard_data["total_monthly_commitments"] += monthly_amount
                    
                    if paid_months >= total_months:
                        dashboard_data["payment_progress"]["completed"] += 1
                    else:
                        dashboard_data["payment_progress"]["in_progress"] += 1
        
        # Sort people by pending amount (highest first)
        dashboard_data["people"] = sorted(
            dashboard_data["people"], 
            key=lambda x: x["pending_amount"], 
            reverse=True
        )
        
        # Add summary statistics
        dashboard_data["total_people"] = len(dashboard_data["people"])
        dashboard_data["average_pending"] = (
            dashboard_data["total_pending"] / dashboard_data["total_people"] 
            if dashboard_data["total_people"] > 0 else 0
        )
        
        return dashboard_data
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving dashboard data: {e}",
        )