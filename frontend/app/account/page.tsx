import { PageHeader } from "@/components/ui/primitives";
import { AccountView } from "@/features/auth/AccountView";

export default function AccountPage() {
  return (
    <>
      <PageHeader title="Account" subtitle="Your usage and sign-in details." />
      <AccountView />
    </>
  );
}
