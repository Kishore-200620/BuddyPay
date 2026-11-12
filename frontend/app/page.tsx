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


type InvoiceCurrencyGroup = {
  id: number;
  currency: string;
  subtotal: string | number | null;
  tax_amount: string | number | null;
  total_amount: string | number | null;
  payment_status: string;
};

type Invoice = {
  id: number;
  invoice_number: string | null;
  seller_name: string | null;
  seller_email?: string | null;
  provider_id: number | null;
  subtotal?: string | number | null;
  tax_amount?: string | number | null;
  total_amount: string | number | null;
  currency: string | null;
  currency_groups: InvoiceCurrencyGroup[];
  status: string;
  payment_status: string | null;
  ai_summary?: string | null;
};


type InvoiceHistory = {
  items: Invoice[];
  total: number;
};
type ProviderMatchCandidate = {
  id: number;
  full_name: string;
  email: string;
};

type ProviderMatchResponse = {
  matched: boolean;
  provider: ProviderMatchCandidate | null;
  message: string;
};

type ProviderAssignmentResponse = {
  invoice_id: number;
  provider_id: number;
  provider_name: string;
  provider_email: string;
  message: string;
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
  const [invoiceFile, setInvoiceFile] = useState<File | null>(null);
const [uploadingInvoice, setUploadingInvoice] = useState(false);
  const [buyerEmail, setBuyerEmail] = useState("");
  const [invoiceNumber, setInvoiceNumber] = useState("");
  const [totalAmount, setTotalAmount] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [providerMatches, setProviderMatches] = useState<
  Record<number, ProviderMatchResponse>
>({});
const [providerMatchLoadingId, setProviderMatchLoadingId] =
  useState<number | null>(null);
const [providerConfirmingId, setProviderConfirmingId] =
  useState<number | null>(null);
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
  async function handleFindProvider(invoiceId: number) {
  if (!accessToken) {
    setError("Please sign in first.");
    return;
  }

  setError("");
  setMessage("");
  setProviderMatchLoadingId(invoiceId);

  try {
    const response = await fetch(
      `${API_BASE}/invoices/${invoiceId}/provider-match`,
      {
        headers: {
          Authorization: `Bearer ${accessToken}`,
        },
      }
    );

    const data = await response.json();

    if (!response.ok) {
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : "Could not find the provider account."
      );
    }

    setProviderMatches((current) => ({
      ...current,
      [invoiceId]: data as ProviderMatchResponse,
    }));
  } catch (err) {
    setError(
      err instanceof Error
        ? err.message
        : "Could not find the provider account."
    );
  } finally {
    setProviderMatchLoadingId(null);
  }
}
async function handleConfirmProvider(invoiceId: number) {
  if (!accessToken) {
    setError("Please sign in first.");
    return;
  }

  setError("");
  setMessage("");
  setProviderConfirmingId(invoiceId);

  try {
    const response = await fetch(
      `${API_BASE}/invoices/${invoiceId}/provider-match/confirm`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${accessToken}`,
        },
      }
    );

    const data = await response.json();

    if (!response.ok) {
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : "Could not confirm the provider account."
      );
    }

    const assignment = data as ProviderAssignmentResponse;

    await loadInvoices(accessToken);

    setProviderMatches((current) => {
      const updated = { ...current };
      delete updated[invoiceId];
      return updated;
    });

    setMessage(
      `Provider ${assignment.provider_name} (${assignment.provider_email}) linked successfully.`
    );
  } catch (err) {
    setError(
      err instanceof Error
        ? err.message
        : "Could not confirm the provider account."
    );
  } finally {
    setProviderConfirmingId(null);
  }
}
async function handleInvoiceUpload(
  event: React.FormEvent<HTMLFormElement>
) {
  event.preventDefault();

  if (!accessToken || !invoiceFile) {
    setError("Please select an invoice PDF first.");
    return;
  }

  if (invoiceFile.type !== "application/pdf") {
    setError("Only PDF invoices are supported.");
    return;
  }

  if (invoiceFile.size > 10 * 1024 * 1024) {
    setError("The invoice PDF must not exceed 10 MB.");
    return;
  }

  setError("");
  setMessage("");
  setUploadingInvoice(true);

  try {
    const formData = new FormData();
    formData.append("file", invoiceFile);

    const response = await fetch(`${API_BASE}/invoices/upload`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
      },
      body: formData,
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : "Invoice upload or extraction failed."
      );
    }

    const uploadedInvoice = data as Invoice;

    setInvoices((current) => [
      uploadedInvoice,
      ...current.filter((invoice) => invoice.id !== uploadedInvoice.id),
    ]);

    setInvoiceFile(null);

    setMessage(
      `Invoice ${uploadedInvoice.invoice_number ?? `#${uploadedInvoice.id}`} uploaded and processed successfully.`
    );

    event.currentTarget.reset();
  } catch (err) {
    setError(
      err instanceof Error
        ? err.message
        : "Could not upload the invoice."
    );
  } finally {
    setUploadingInvoice(false);
  }
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


async function handleCheckout(
  invoice: Invoice,
  currencyGroupId?: number
) {
  if (!accessToken) {
    setError("Please sign in first.");
    return;
  }

  setError("");
  setMessage("");
  setLoading(true);

  try {
    const query = new URLSearchParams();

    if (currencyGroupId !== undefined) {
      query.set("currency_group_id", String(currencyGroupId));
    }

    const queryString = query.toString();
    const checkoutUrl =
      `${API_BASE}/paypal/invoices/${invoice.id}/orders` +
      (queryString ? `?${queryString}` : "");

    const response = await fetch(checkoutUrl, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
      },
    });

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

<div className="mb-6 rounded-xl border border-blue-200 bg-white p-5 shadow-sm">
  <h3 className="text-lg font-semibold">Upload an invoice</h3>
  <p className="mt-1 text-sm text-slate-600">
    Upload a PDF invoice. BuddyPay will extract its details and organize
    the amounts by currency.
  </p>

  <form onSubmit={handleInvoiceUpload} className="mt-4 space-y-3">
    <input
      type="file"
      accept="application/pdf,.pdf"
      required
      onChange={(event) =>
        setInvoiceFile(event.target.files?.[0] ?? null)
      }
      className="block w-full rounded-lg border border-slate-300 p-3 text-sm"
    />

    <button
      type="submit"
      disabled={!invoiceFile || uploadingInvoice || loading}
      className="rounded-lg bg-blue-700 px-5 py-3 font-semibold text-white hover:bg-blue-800 disabled:opacity-50"
    >
      {uploadingInvoice ? "Uploading and extracting..." : "Upload PDF"}
    </button>
  </form>
</div>

            {loading ? (
              <p className="text-slate-600">Loading...</p>
            ) : invoices.length === 0 ? (
              <div className="rounded-xl border border-slate-200 bg-white p-6 text-slate-600">
                No invoices found for this buyer.
              </div>
                        ) : (
              <div className="space-y-4">
                {invoices.map((invoice) => {
                  const invoiceProcessed =
                    invoice.status?.toLowerCase() === "processed";

                  const currencyGroups = invoice.currency_groups ?? [];

                  const isActivePayment = (status: string | null | undefined) =>
                    ["initializing", "created", "approved", "capturing", "pending"].includes(
                      status?.toLowerCase() ?? ""
                    );

                  return (
                    <article
                      key={invoice.id}
                      className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
                    >
                      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                        <div>
                          <h3 className="font-semibold">
                            {invoice.invoice_number || `Invoice #${invoice.id}`}
                          </h3>

                          <p className="mt-1 text-sm text-slate-600">
                            Provider: {invoice.seller_name || "Not specified"}
                          </p>

                          <p className="mt-1 text-sm text-slate-500">
                            Invoice status: {invoice.status}
                          </p>
                        </div>

                        <span className="text-sm font-medium text-slate-600">
                          Payment: {invoice.payment_status ?? "unpaid"}
                        </span>
                      </div>

                      {!invoiceProcessed && (
                        <p className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
                          Payment is unavailable until invoice processing is complete.
                        </p>
                      )}

                      {!invoice.provider_id && (
  <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-4">
    <p className="text-sm text-amber-800">
      Provider account is not linked. Payment cannot be initiated yet.
    </p>

    {invoiceProcessed && (
      <div className="mt-3">
        {!providerMatches[invoice.id] ? (
          <button
            type="button"
            onClick={() => void handleFindProvider(invoice.id)}
            disabled={
              providerMatchLoadingId === invoice.id ||
              providerConfirmingId === invoice.id
            }
            className="rounded-lg bg-slate-800 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-900 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {providerMatchLoadingId === invoice.id
              ? "Searching provider..."
              : "Find provider account"}
          </button>
        ) : providerMatches[invoice.id].matched &&
  providerMatches[invoice.id].provider !== null ? (
          <div className="rounded-lg border border-blue-200 bg-white p-4">
            <p className="text-sm font-semibold text-slate-800">
              Provider account candidate found
            </p>

            <p className="mt-2 text-sm text-slate-700">
              Name: {providerMatches[invoice.id].provider?.full_name}
            </p>

            <p className="text-sm text-slate-700">
              Email: {providerMatches[invoice.id].provider?.email}
            </p>

            <p className="mt-2 text-xs text-amber-700">
              Confirm only if this account belongs to the seller shown on
              your invoice. This match does not independently verify legal identity.
            </p>

            <button
              type="button"
              onClick={() => void handleConfirmProvider(invoice.id)}
              disabled={
                providerConfirmingId === invoice.id ||
                providerMatchLoadingId === invoice.id
              }
              className="mt-3 rounded-lg bg-blue-700 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-800 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {providerConfirmingId === invoice.id
                ? "Confirming..."
                : "Confirm provider account"}
            </button>
          </div>
        ) : (
          <div className="mt-2">
            <p className="text-sm text-slate-700">
              {providerMatches[invoice.id].message ||
                "No matching active provider account was found."}
            </p>

            <button
              type="button"
              onClick={() => void handleFindProvider(invoice.id)}
              disabled={providerMatchLoadingId === invoice.id}
              className="mt-2 rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-100 disabled:opacity-50"
            >
              Search again
            </button>
          </div>
        )}
      </div>
    )}
  </div>
)}

                      {currencyGroups.length > 0 ? (
                        <div className="mt-4 space-y-3">
                          <h4 className="text-sm font-semibold text-slate-700">
                            Amounts by currency
                          </h4>

                          {currencyGroups.map((group) => {
                            const groupStatus =
                              group.payment_status?.toLowerCase() ?? "unpaid";

                            const amount = Number(group.total_amount);

                            const alreadyPaid = groupStatus === "completed";
                            const paymentInProgress = isActivePayment(groupStatus);

                            const canPay =
                              invoiceProcessed &&
                              Boolean(invoice.provider_id) &&
                              Number.isFinite(amount) &&
                              amount > 0 &&
                              groupStatus === "unpaid";

                            return (
                              <div
                                key={group.id}
                                className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-slate-50 p-4 sm:flex-row sm:items-center sm:justify-between"
                              >
                                <div>
                                  <p className="text-lg font-bold">
                                    {group.total_amount ?? "Amount unavailable"}{" "}
                                    {group.currency}
                                  </p>

                                  <p className="text-sm text-slate-600">
                                    Payment status: {groupStatus}
                                  </p>
                                </div>

                                <button
                                  type="button"
                                  onClick={() =>
                                    void handleCheckout(invoice, group.id)
                                  }
                                  disabled={loading || !canPay}
                                  className="rounded-lg bg-blue-700 px-5 py-3 font-semibold text-white hover:bg-blue-800 disabled:cursor-not-allowed disabled:opacity-50"
                                >
                                  {alreadyPaid
                                    ? "Already paid"
                                    : paymentInProgress
                                      ? "Payment in progress"
                                      : !invoiceProcessed
                                        ? "Processing invoice"
                                        : !invoice.provider_id
                                          ? "Provider unavailable"
                                          : groupStatus !== "unpaid"
                                            ? "Payment unavailable"
                                            : "Pay with PayPal"}
                                </button>
                              </div>
                            );
                          })}
                        </div>
                      ) : (
                        <div className="mt-4 flex flex-col gap-3 rounded-lg border border-slate-200 bg-slate-50 p-4 sm:flex-row sm:items-center sm:justify-between">
                          <div>
                            <p className="text-lg font-bold">
                              {invoice.total_amount ?? "Amount unavailable"}{" "}
                              {invoice.currency}
                            </p>
                            <p className="text-sm text-slate-600">
                              Payment status: {invoice.payment_status ?? "unpaid"}
                            </p>
                          </div>

                          <button
                            type="button"
                            onClick={() => void handleCheckout(invoice)}
                            disabled={
                              loading ||
                              !invoiceProcessed ||
                              !invoice.provider_id ||
                              !Number.isFinite(Number(invoice.total_amount)) ||
                              Number(invoice.total_amount) <= 0 ||
                              (invoice.payment_status?.toLowerCase() ?? "unpaid") !==
                                "unpaid"
                            }
                            className="rounded-lg bg-blue-700 px-5 py-3 font-semibold text-white hover:bg-blue-800 disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            {invoice.payment_status?.toLowerCase() === "completed"
                              ? "Already paid"
                              : isActivePayment(invoice.payment_status)
                                ? "Payment in progress"
                                : !invoiceProcessed
                                  ? "Processing invoice"
                                  : !invoice.provider_id
                                    ? "Provider unavailable"
                                    : invoice.payment_status?.toLowerCase() !== "unpaid"
                                      ? "Payment unavailable"
                                      : "Pay with PayPal"}
                          </button>
                        </div>
                      )}
                    </article>
                  );
                })}
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