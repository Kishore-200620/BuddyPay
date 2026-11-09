
"use client";

import { useEffect, useRef, useState } from "react";

const API_BASE = "http://127.0.0.1:8000/api/v1";
const TOKEN_KEY = "buddypay_access_token";

type CaptureResult = {
  payment_id?: number;
  paypal_order_id?: string;
  paypal_capture_id?: string | null;
  status?: string;
  paypal_status?: string;
};

export default function PayPalReturnPage() {
  const [result, setResult] = useState<CaptureResult | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const captureStarted = useRef(false);

  useEffect(() => {
    if (captureStarted.current) return;
    captureStarted.current = true;

    const orderId = new URLSearchParams(
      window.location.search
    ).get("token");

    const accessToken = window.localStorage.getItem(TOKEN_KEY);

    if (!orderId) {
      setError("PayPal order ID is missing.");
      setLoading(false);
      return;
    }

    if (!accessToken) {
      setError("Your buyer session has expired. Please sign in.");
      setLoading(false);
      return;
    }

    async function captureOrder() {
      try {
        const response = await fetch(
          `${API_BASE}/paypal/invoices/orders/${encodeURIComponent(orderId!)}/capture`,
          {
            method: "POST",
            headers: {
              Authorization: `Bearer ${accessToken}`,
            },
          }
        );

        const data = await response.json();

        if (!response.ok || !("payment_id" in data)) {
          throw new Error(
            typeof data.detail === "string"
              ? data.detail
              : "Payment status could not be confirmed."
          );
        }

        setResult(data as CaptureResult);
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "An unexpected error occurred."
        );
      } finally {
        setLoading(false);
      }
    }

    void captureOrder();
  }, []);

  const paymentStatus = result?.status?.toUpperCase();

  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col items-center justify-center gap-4 p-8 text-center">
      {loading ? (
        <>
          <h1 className="text-2xl font-semibold">
            Processing your payment...
          </h1>
          <p>Please wait while BuddyPay checks your payment.</p>
        </>
      ) : error ? (
        <>
          <h1 className="text-2xl font-semibold">
            Payment requires attention
          </h1>
          <p>{error}</p>
          <a href="/" className="mt-2 rounded-lg bg-blue-700 px-5 py-3 font-semibold text-white">
            Return to BuddyPay
          </a>
        </>
      ) : (
        <>
          <h1 className="text-2xl font-semibold">
            {paymentStatus === "COMPLETED"
              ? "Payment confirmed"
              : paymentStatus === "PENDING"
                ? "Payment is pending"
                : paymentStatus === "CAPTURING"
                  ? "Payment is being reconciled"
                  : "Payment status received"}
          </h1>

          <p>BuddyPay status: {result?.status ?? "Unknown"}</p>
          <p>Payment ID: {result?.payment_id ?? "Unavailable"}</p>
          <p>PayPal order ID: {result?.paypal_order_id ?? "Unavailable"}</p>
          <p>PayPal capture ID: {result?.paypal_capture_id ?? "Unavailable"}</p>

          <a href="/" className="mt-2 rounded-lg bg-blue-700 px-5 py-3 font-semibold text-white">
            Return to BuddyPay
          </a>
        </>
      )}
    </main>
  );
}
