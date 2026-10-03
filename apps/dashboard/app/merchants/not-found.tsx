import { ButtonLink } from "../_components/ui/button";
import { EmptyState } from "../_components/ui/empty-state";

export default function MerchantNotFound() {
  return (
    <EmptyState title="No such merchant" action={<ButtonLink href="/merchants">Back to merchants</ButtonLink>}>
      The Okwan API has no merchant with that id under your account.
    </EmptyState>
  );
}
