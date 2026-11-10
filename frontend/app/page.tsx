"use client";
import BuddyCave from "@/components/BuddyCave";
import { useEffect, useState } from "react";

const API_BASE = "http://127.0.0.1:8000/api/v1";
const TOKEN_KEY = "buddypay_access_token";

type UserProfile = {
  id: number;
  full_name: string;
  email: string;
  role: "buyer" | "provider";
  is_active: boolean;
};

type Invoice = {
  id: number;
  invoice_number: string | null;
  seller_name: string | null;
  provider_id: number | null;
  total_amount: string | number | null;
  currency: string;
  status: string;
  payment_status: string | null;
};

type InvoiceHistory = {
  items: Invoice[];
  total: number;
};

type LoginResponse = {
  access_token: string;
  token_type: string;
  expires_in: number;
};

type CreateOrderResponse = {
  payment_id: number;
  invoice_id: number;
  paypal_order_id: string;
  amount: string;
  currency: string;
  status: string;
  approval_url: string | null;
};

type ProviderInvoiceResponse = {
  id: number;
  invoice_number: string | null;
  total_amount: string | number | null;
  currency: string;
};

export default function Home() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [accessToken, setAccessToken] = useState("");
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [buyerEmail, setBuyerEmail] = useState("");
  const [invoiceNumber, setInvoiceNumber] = useState("");
  const [totalAmount, setTotalAmount] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  async function fetchProfile(token: string): Promise<UserProfile> {
    const response = await fetch(`${API_BASE}/users/me`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const data = await response.json();

    if (!response.ok) {
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : "Could not load your profile."
      );
    }

    return data as UserProfile;
  }

  async function loadInvoices(token: string) {
    const response = await fetch(`${API_BASE}/invoices/`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const data = await response.json();

    if (!response.ok) {
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : "Could not load invoices."
      );
    }

    setInvoices((data as InvoiceHistory).items ?? []);
  }

  async function initializeSession(token: string) {
    const user = await fetchProfile(token);
    setProfile(user);

    if (user.role === "buyer") {
      await loadInvoices(token);
    } else {
      setInvoices([]);
    }
  }

  useEffect(() => {
    const savedToken = window.localStorage.getItem(TOKEN_KEY);
    if (!savedToken) return;

    setAccessToken(savedToken);
    setLoading(true);

    initializeSession(savedToken)
      .catch((err: unknown) => {
        window.localStorage.removeItem(TOKEN_KEY);
        setAccessToken("");
        setProfile(null);
        setError(
          err instanceof Error ? err.message : "Could not restore your session."
        );
      })
      .finally(() => setLoading(false));
  }, []);

  async function handleLogin(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setMessage("");
    setLoading(true);

    try {
      const form = new URLSearchParams();
      form.set("username", email);
      form.set("password", password);

      const response = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: form.toString(),
      });

      const data = (await response.json()) as
        | LoginResponse
        | { detail?: string };

      if (!response.ok || !("access_token" in data)) {
        throw new Error(
          "detail" in data && typeof data.detail === "string"
            ? data.detail
            : "Login failed."
        );
      }

      window.localStorage.setItem(TOKEN_KEY, data.access_token);
      setAccessToken(data.access_token);
      setPassword("");

      await initializeSession(data.access_token);
      setMessage("Signed in successfully.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed.");
    } finally {
      setLoading(false);
    }
  }

  async function handleCreateInvoice(
    event: React.FormEvent<HTMLFormElement>
  ) {
    event.preventDefault();

    if (!accessToken) {
      setError("Please sign in first.");
      return;
    }

    setError("");
    setMessage("");
    setLoading(true);

    try {
      const response = await fetch(`${API_BASE}/invoices/provider`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${accessToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          buyer_email: buyerEmail.trim(),
          invoice_number: invoiceNumber.trim(),
          total_amount: totalAmount,
          currency,
        }),
      });

      const data = (await response.json()) as
        | ProviderInvoiceResponse
        | { detail?: string };

      if (!response.ok || !("id" in data)) {
        throw new Error(
          "detail" in data && typeof data.detail === "string"
            ? data.detail
            : "Could not create invoice."
        );
      }

      setMessage(
        `Invoice ${data.invoice_number ?? `#${data.id}`} created for ${buyerEmail.trim()}. The buyer can now sign in to pay.`
      );
      setBuyerEmail("");
      setInvoiceNumber("");
      setTotalAmount("");
      setCurrency("USD");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not create invoice."
      );
    } finally {
      setLoading(false);
    }
  }

  async function handleCheckout(invoice: Invoice) {
    if (!accessToken) {
      setError("Please sign in first.");
      return;
    }

    setError("");
    setMessage("");
    setLoading(true);

    try {
      const response = await fetch(
        `${API_BASE}/paypal/invoices/${invoice.id}/orders`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${accessToken}` },
        }
      );

      const data = (await response.json()) as
        | CreateOrderResponse
        | { detail?: string };

      if (!response.ok || !("approval_url" in data)) {
        throw new Error(
          "detail" in data && typeof data.detail === "string"
            ? data.detail
            : "Could not create PayPal checkout."
        );
      }

      if (!data.approval_url) {
        throw new Error("PayPal did not return an approval URL.");
      }

      window.location.assign(data.approval_url);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not start checkout."
      );
      setLoading(false);
    }
  }

  function handleLogout() {
    window.localStorage.removeItem(TOKEN_KEY);
    setAccessToken("");
    setProfile(null);
    setInvoices([]);
    setError("");
    setMessage("Signed out.");
  }

  return (
    <main className="min-h-screen bg-slate-50 px-4 py-10 text-slate-900 sm:px-8">
      <div className="mx-auto max-w-4xl">
        <header className="mb-10 flex items-center justify-between">
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.2em] text-blue-700">
              BuddyPay
            </p>
            <h1 className="mt-2 text-3xl font-bold">Invoice payments</h1>
            <p className="mt-2 text-slate-600">
              Create invoices or pay securely with PayPal Sandbox.
            </p>
          </div>

          {accessToken && (
            <button
              onClick={handleLogout}
              className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium hover:bg-white"
            >
              Sign out
            </button>
          )}
        </header>

        {error && (
          <div role="alert" className="mb-6 rounded-lg bg-red-50 p-4 text-red-700">
            {error}
          </div>
        )}

        {message && (
          <div className="mb-6 rounded-lg bg-green-50 p-4 text-green-700">
            {message}
          </div>
        )}

        {!accessToken ? (
          <section className="max-w-md rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <h2 className="text-xl font-semibold">Sign in</h2>
            <form onSubmit={handleLogin} className="mt-5 space-y-4">
              <label className="block">
                <span className="mb-1 block text-sm font-medium">Email</span>
                <input
                  type="email"
                  autoComplete="username"
                  required
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 outline-none focus:border-blue-600"
                />
              </label>

              <label className="block">
                <span className="mb-1 block text-sm font-medium">Password</span>
                <input
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 outline-none focus:border-blue-600"
                />
              </label>

              <button
                type="submit"
                disabled={loading}
                className="w-full rounded-lg bg-blue-700 px-4 py-3 font-semibold text-white hover:bg-blue-800 disabled:opacity-50"
              >
                {loading ? "Signing in..." : "Sign in"}
              </button>
            </form>
          </section>
        ) : loading && !profile ? (
          <p className="text-slate-600">Loading your account...</p>
        ) : profile?.role === "provider" ? (
          <section className="max-w-xl rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <h2 className="text-xl font-semibold">Create an invoice</h2>
            <p className="mt-2 text-sm text-slate-600">
              Signed in as provider: {profile.full_name}
            </p>

            <form onSubmit={handleCreateInvoice} className="mt-5 space-y-4">
              <label className="block">
                <span className="mb-1 block text-sm font-medium">
                  Buyer email
                </span>
                <input
                  type="email"
                  required
                  value={buyerEmail}
                  onChange={(event) => setBuyerEmail(event.target.value)}
                  placeholder="priya@example.com"
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 outline-none focus:border-blue-600"
                />
              </label>

              <label className="block">
                <span className="mb-1 block text-sm font-medium">
                  Invoice number
                </span>
                <input
                  required
                  maxLength={100}
                  value={invoiceNumber}
                  onChange={(event) => setInvoiceNumber(event.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 outline-none focus:border-blue-600"
                />
              </label>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <label className="block">
                  <span className="mb-1 block text-sm font-medium">
                    Total amount
                  </span>
                  <input
                    type="number"
                    min="0.01"
                    step="0.01"
                    required
                    value={totalAmount}
                    onChange={(event) => setTotalAmount(event.target.value)}
                    className="w-full rounded-lg border border-slate-300 px-3 py-2 outline-none focus:border-blue-600"
                  />
                </label>

                <label className="block">
                  <span className="mb-1 block text-sm font-medium">
                    Currency
                  </span>
                  <select
                    value={currency}
                    onChange={(event) => setCurrency(event.target.value)}
                    className="w-full rounded-lg border border-slate-300 px-3 py-2 outline-none focus:border-blue-600"
                  >
                    <option value="USD">USD</option>
                    <option value="INR">INR</option>
                  </select>
                </label>
              </div>

              <button
                type="submit"
                disabled={loading}
                className="w-full rounded-lg bg-blue-700 px-4 py-3 font-semibold text-white hover:bg-blue-800 disabled:opacity-50"
              >
                {loading ? "Creating invoice..." : "Create invoice"}
              </button>
            </form>
          </section>
        ) : profile?.role === "buyer" ? (
          <section>
            <h2 className="mb-4 text-xl font-semibold">Your invoices</h2>

            {loading ? (
              <p className="text-slate-600">Loading...</p>
            ) : invoices.length === 0 ? (
              <div className="rounded-xl border border-slate-200 bg-white p-6 text-slate-600">
                No invoices found for this buyer.
              </div>
            ) : (
              <div className="space-y-4">
                {invoices.map((invoice) => (
                  <article
                    key={invoice.id}
                    className="flex flex-col gap-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div>
                      <h3 className="font-semibold">
                        {invoice.invoice_number || `Invoice #${invoice.id}`}
                      </h3>
                      <p className="mt-1 text-sm text-slate-600">
                        Provider: {invoice.seller_name || "Not specified"}
                      </p>
                      <p className="mt-2 text-lg font-bold">
                        {invoice.total_amount ?? "Amount unavailable"}{" "}
                        {invoice.currency}
                      </p>
                      <p className="text-sm text-slate-500">
                        Invoice status: {invoice.status}
                      </p>
                      <p className="text-sm font-medium text-slate-600">
                        Payment status: {invoice.payment_status ?? "unpaid"}
                      </p>
                    </div>

                    <button
                      onClick={() => void handleCheckout(invoice)}
                      disabled={
                        loading ||
                        !invoice.provider_id ||
                        !invoice.total_amount ||
                        invoice.payment_status?.toLowerCase() === "completed"
                      }
                      className="rounded-lg bg-blue-700 px-5 py-3 font-semibold text-white hover:bg-blue-800 disabled:opacity-50"
                    >
                      {!invoice.provider_id
                        ? "Provider unavailable"
                        : invoice.payment_status?.toLowerCase() === "completed"
                          ? "Already paid"
                          : "Pay with PayPal"}
                    </button>
                  </article>
                ))}
              </div>
            )}
          </section>
        ) : (
          <p className="text-slate-600">Unsupported account role.</p>
        )}
        {profile && accessToken && (
          <BuddyCave
            accessToken={accessToken}
            role={profile.role}
          />
        )}
      </div>
    </main>
  );
}