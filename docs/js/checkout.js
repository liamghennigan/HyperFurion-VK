// Hosted Stripe checkout is archived. There is no subscription to buy.
// The unpublished relay lives in archive/relay/. Do not restore payment
// links here as a launch path.
export const CHECKOUT = { basic: "", pro: "" };
export const live = () => false;
export const checkoutLive = false;
