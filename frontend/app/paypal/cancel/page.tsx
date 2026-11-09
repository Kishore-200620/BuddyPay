
export default function PayPalCancelPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col items-center justify-center gap-4 p-8 text-center">
      <h1 className="text-2xl font-semibold">
        Checkout cancelled
      </h1>

      <p>
        You cancelled the PayPal checkout.
        No new payment capture was requested by this page.
      </p>

      <a
        href="/"
        className="mt-2 rounded-lg bg-blue-700 px-5 py-3 font-semibold text-white"
      >
        Return to BuddyPay
      </a>
    </main>
  );
}
