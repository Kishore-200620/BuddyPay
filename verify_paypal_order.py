import asyncio
from app.services.paypal_service import get_paypal_us_order

async def main():
    order = await get_paypal_us_order("63R24516WA282650H")

    print("PayPal Order ID:", order.get("id"))
    print("PayPal Order Status:", order.get("status"))

    captures = []

    for unit in order.get("purchase_units", []):
        payments = unit.get("payments") or {}
        captures.extend(payments.get("captures") or [])

    print("\nTotal capture records:", len(captures))

    for index, capture in enumerate(captures, start=1):
        amount = capture.get("amount") or {}

        print(f"\nCapture {index}")
        print("Capture ID:", capture.get("id"))
        print("Status:", capture.get("status"))
        print("Amount:", amount.get("value"), amount.get("currency_code"))

asyncio.run(main())
