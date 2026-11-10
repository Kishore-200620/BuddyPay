"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  type ColDef,
} from "ag-grid-community";

ModuleRegistry.registerModules([AllCommunityModule]);

const API_BASE = "http://127.0.0.1:8000/api/v1";

type UserRole = "buyer" | "provider";

type Payment = {
  id: number;
  invoice_id: number;
  buyer_id: number;
  provider_id: number;
  paypal_order_id: string | null;
  paypal_capture_id: string | null;
  amount: string | number;
  currency: string;
  status: string;
  created_at: string;
  updated_at: string;
};

type PaymentHistory = {
  items: Payment[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
};

type CurrencyTotal = {
  currency: string;
  completed_amount: string;
  pending_amount: string;
};

type PaymentSummary = {
  total_payments: number;
  completed_count: number;
  pending_count: number;
  failed_count: number;
  cancelled_count: number;
  status_counts: Record<string, number>;
  currency_totals: CurrencyTotal[];
};

type BuddyCaveProps = {
  accessToken: string;
  role: UserRole;
};

function formatMoney(amount: string | number, currency: string) {
  const numericAmount = Number(amount);

  if (!Number.isFinite(numericAmount)) {
    return `${amount} ${currency}`;
  }

  try {
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(numericAmount);
  } catch {
    return `${numericAmount.toFixed(2)} ${currency}`;
  }
}

function formatDate(value: string) {
  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("en-IN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function statusClass(status: string) {
  switch (status.toLowerCase()) {
    case "completed":
      return "bg-green-100 text-green-800";
    case "failed":
      return "bg-red-100 text-red-800";
    case "cancelled":
      return "bg-slate-100 text-slate-700";
    case "pending":
    case "created":
    case "approved":
    case "capturing":
    case "initializing":
      return "bg-amber-100 text-amber-800";
    default:
      return "bg-blue-100 text-blue-800";
  }
}

export default function BuddyCave({
  accessToken,
  role,
}: BuddyCaveProps) {
  const [summary, setSummary] = useState<PaymentSummary | null>(null);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [selectedPayment, setSelectedPayment] = useState<Payment | null>(
    null,
  );
  const [searchText, setSearchText] = useState("");
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(0);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");

  const pageSize = 10;

  const authHeaders = useMemo(
    () => ({
      Authorization: `Bearer ${accessToken}`,
    }),
    [accessToken],
  );

  const loadDashboard = useCallback(async () => {
    if (!accessToken) return;

    setLoading(true);
    setError("");

    try {
      const [summaryResponse, historyResponse] = await Promise.all([
        fetch(`${API_BASE}/payments/summary`, {
          headers: authHeaders,
          cache: "no-store",
        }),
        fetch(
          `${API_BASE}/payments/history?page=${page}&page_size=${pageSize}`,
          {
            headers: authHeaders,
            cache: "no-store",
          },
        ),
      ]);

      const summaryData = await summaryResponse.json();
      const historyData = await historyResponse.json();

      if (!summaryResponse.ok) {
        throw new Error(
          typeof summaryData.detail === "string"
            ? summaryData.detail
            : "Could not load payment summary.",
        );
      }

      if (!historyResponse.ok) {
        throw new Error(
          typeof historyData.detail === "string"
            ? historyData.detail
            : "Could not load payment history.",
        );
      }

      setSummary(summaryData as PaymentSummary);

      const history = historyData as PaymentHistory;
      setPayments(history.items ?? []);
      setTotalPages(history.total_pages ?? 0);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not load BuddyCave dashboard.",
      );
    } finally {
      setLoading(false);
    }
  }, [accessToken, authHeaders, page]);

  useEffect(() => {
    void loadDashboard();
  }, [loadDashboard]);

  async function openPaymentDetails(paymentId: number) {
    setDetailLoading(true);
    setError("");
    setSelectedPayment(null);

    try {
      const response = await fetch(
        `${API_BASE}/payments/${paymentId}`,
        {
          headers: authHeaders,
          cache: "no-store",
        },
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          typeof data.detail === "string"
            ? data.detail
            : "Could not load transaction details.",
        );
      }

      setSelectedPayment(data as Payment);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not load transaction details.",
      );
    } finally {
      setDetailLoading(false);
    }
  }

  const columnDefs = useMemo<ColDef<Payment>[]>(
    () => [
      {
        field: "id",
        headerName: "Payment ID",
        width: 115,
        filter: "agNumberColumnFilter",
      },
      {
        field: "invoice_id",
        headerName: "Invoice ID",
        width: 115,
        filter: "agNumberColumnFilter",
      },
      {
        field: "amount",
        headerName: "Amount",
        minWidth: 160,
        valueFormatter: (params) =>
          params.data
            ? formatMoney(params.value, params.data.currency)
            : "",
      },
      {
        field: "currency",
        headerName: "Currency",
        width: 110,
      },
      {
        field: "status",
        headerName: "Status",
        width: 140,
        cellRenderer: (params: { value: string }) => (
          <span
            className={`inline-flex rounded-full px-3 py-1 text-xs font-semibold ${statusClass(
              params.value ?? "",
            )}`}
          >
            {(params.value ?? "unknown").toUpperCase()}
          </span>
        ),
      },
      {
        field: "paypal_order_id",
        headerName: "PayPal Order ID",
        minWidth: 190,
        flex: 1,
        valueFormatter: (params) => params.value ?? "—",
      },
      {
        field: "created_at",
        headerName: "Created",
        minWidth: 190,
        valueFormatter: (params) =>
          params.value ? formatDate(params.value) : "—",
      },
      {
        headerName: "Details",
        width: 115,
        sortable: false,
        filter: false,
        cellRenderer: (params: { data?: Payment }) => (
          <button
            type="button"
            onClick={() => {
              if (params.data) {
                void openPaymentDetails(params.data.id);
              }
            }}
            className="font-semibold text-blue-700 hover:text-blue-900"
          >
            View
          </button>
        ),
      },
    ],
    [authHeaders],
  );

  const filteredPayments = useMemo(() => {
    const query = searchText.trim().toLowerCase();

    if (!query) return payments;

    return payments.filter((payment) =>
      [
        payment.id,
        payment.invoice_id,
        payment.amount,
        payment.currency,
        payment.status,
        payment.paypal_order_id ?? "",
        payment.paypal_capture_id ?? "",
      ]
        .join(" ")
        .toLowerCase()
        .includes(query),
    );
  }, [payments, searchText]);

  const gridTheme = useMemo(
    () =>
      themeQuartz.withParams({
        accentColor: "#2563eb",
        borderRadius: 10,
        spacing: 7,
      }),
    [],
  );

  return (
    <section className="mt-10 space-y-6">
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <p className="text-sm font-semibold uppercase tracking-[0.18em] text-blue-700">
            BuddyCave
          </p>
          <h2 className="mt-1 text-2xl font-bold text-slate-900">
            Financial control center
          </h2>
          <p className="mt-1 text-sm text-slate-600">
            {role === "buyer"
              ? "Track the payments you have made."
              : "Track the payments received from your buyers."}
          </p>
        </div>

        <button
          type="button"
          onClick={() => void loadDashboard()}
          disabled={loading}
          className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        >
          {loading ? "Refreshing..." : "Refresh data"}
        </button>
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700"
        >
          {error}
        </div>
      )}

      {loading && !summary ? (
        <div className="rounded-xl border border-slate-200 bg-white p-8 text-center text-slate-600">
          Loading your financial dashboard...
        </div>
      ) : summary ? (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
            <SummaryCard
              label="Total payments"
              value={summary.total_payments}
              description="All recorded transactions"
            />
            <SummaryCard
              label="Completed"
              value={summary.completed_count}
              description="Successfully captured"
              valueClass="text-green-700"
            />
            <SummaryCard
              label="In progress"
              value={summary.pending_count}
              description="Awaiting completion"
              valueClass="text-amber-700"
            />
            <SummaryCard
              label="Failed"
              value={summary.failed_count}
              description="Unsuccessful transactions"
              valueClass="text-red-700"
            />
            <SummaryCard
              label="Cancelled"
              value={summary.cancelled_count}
              description="Cancelled transactions"
            />
          </div>

          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <h3 className="text-lg font-semibold text-slate-900">
              Currency overview
            </h3>
            <p className="mt-1 text-sm text-slate-500">
              Amounts are grouped by currency. Different currencies are never
              added together.
            </p>

            {summary.currency_totals.length === 0 ? (
              <p className="mt-5 text-sm text-slate-500">
                No payment totals available yet.
              </p>
            ) : (
              <div className="mt-5 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
                {summary.currency_totals.map((item) => (
                  <div
                    key={item.currency}
                    className="rounded-lg border border-slate-200 bg-slate-50 p-4"
                  >
                    <p className="text-sm font-semibold text-slate-500">
                      {item.currency}
                    </p>
                    <p className="mt-2 text-xl font-bold text-green-700">
                      {formatMoney(item.completed_amount, item.currency)}
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      Completed amount
                    </p>
                    <p className="mt-4 text-lg font-semibold text-amber-700">
                      {formatMoney(item.pending_amount, item.currency)}
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      Pending amount
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      ) : null}

      <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
        <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
          <div>
            <h3 className="text-lg font-semibold text-slate-900">
              Transaction history
            </h3>
            <p className="mt-1 text-sm text-slate-500">
              {summary?.total_payments ?? 0} recorded transactions
            </p>
          </div>

          <input
            type="search"
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
            placeholder="Search this page..."
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-600 sm:max-w-xs"
          />
        </div>

        <div className="mt-5 overflow-hidden rounded-lg">
          <AgGridReact<Payment>
            theme={gridTheme}
            rowData={filteredPayments}
            columnDefs={columnDefs}
            defaultColDef={{
              sortable: true,
              filter: true,
              resizable: true,
            }}
            getRowId={(params) => String(params.data.id)}
            domLayout="autoHeight"
            overlayNoRowsTemplate="No transactions found"
            suppressCellFocus
          />
        </div>

        <div className="mt-4 flex flex-col items-center justify-between gap-3 sm:flex-row">
          <p className="text-sm text-slate-500">
            Page {page} of {Math.max(totalPages, 1)}
          </p>

          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => {
                setSelectedPayment(null);
                setPage((current) => Math.max(1, current - 1));
              }}
              disabled={page <= 1 || loading}
              className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium disabled:opacity-40"
            >
              Previous
            </button>
            <button
              type="button"
              onClick={() => {
                setSelectedPayment(null);
                setPage((current) =>
                  Math.min(totalPages, current + 1),
                );
              }}
              disabled={page >= totalPages || loading || totalPages === 0}
              className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      </div>

      {detailLoading && (
        <div className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-600">
          Loading transaction details...
        </div>
      )}

      {selectedPayment && (
        <div className="rounded-xl border border-blue-200 bg-white p-5 shadow-sm">
          <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
            <div>
              <p className="text-sm font-semibold uppercase tracking-wider text-blue-700">
                Transaction details
              </p>
              <h3 className="mt-1 text-xl font-bold text-slate-900">
                Payment #{selectedPayment.id}
              </h3>
            </div>

            <button
              type="button"
              onClick={() => setSelectedPayment(null)}
              className="self-start rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium hover:bg-slate-50"
            >
              Close
            </button>
          </div>

          <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <DetailItem
              label="Amount"
              value={formatMoney(
                selectedPayment.amount,
                selectedPayment.currency,
              )}
            />
            <DetailItem
              label="Status"
              value={selectedPayment.status.toUpperCase()}
            />
            <DetailItem
              label="Invoice ID"
              value={`#${selectedPayment.invoice_id}`}
            />
            <DetailItem
              label="PayPal order ID"
              value={selectedPayment.paypal_order_id ?? "Not available"}
            />
            <DetailItem
              label="PayPal capture ID"
              value={selectedPayment.paypal_capture_id ?? "Not available"}
            />
            <DetailItem
              label="Created"
              value={formatDate(selectedPayment.created_at)}
            />
            <DetailItem
              label="Last updated"
              value={formatDate(selectedPayment.updated_at)}
            />
          </div>
        </div>
      )}
    </section>
  );
}

function SummaryCard({
  label,
  value,
  description,
  valueClass = "text-slate-900",
}: {
  label: string;
  value: number;
  description: string;
  valueClass?: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <p className="text-sm font-medium text-slate-500">{label}</p>
      <p className={`mt-2 text-3xl font-bold ${valueClass}`}>{value}</p>
      <p className="mt-2 text-xs text-slate-500">{description}</p>
    </div>
  );
}

function DetailItem({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="min-w-0 rounded-lg bg-slate-50 p-4">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        {label}
      </p>
      <p className="mt-2 break-all text-sm font-medium text-slate-900">
        {value}
      </p>
    </div>
  );
}