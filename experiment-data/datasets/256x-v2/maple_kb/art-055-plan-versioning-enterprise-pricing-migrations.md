# Plan Versioning — Enterprise Pricing Plan Migrations

**Audience:** Internal — Sales, Solutions Engineering, Customer Success  
**Status:** Generally Available as of March 2026  
**Applies to:** Enterprise and mid-market accounts on volume-tier or custom pricing plans

---

## What is Plan Versioning?

Plan Versioning is a Maple capability that allows pricing plan configurations to be defined, versioned, and migrated across accounts in a controlled way. Instead of requiring a full cancel-and-resubscribe cycle when a customer's pricing structure changes, Plan Versioning lets you create a new version of a plan and migrate the account to it without disrupting billing history, seat assignments, or active subscriptions.

Each plan version is an immutable snapshot of a pricing configuration: tier thresholds, per-seat costs, overage rates, billing frequency, and any custom enterprise terms. When a plan evolves, a new version is created alongside the old one. Accounts remain on their current version until explicitly migrated.

---

## Key Capabilities

**Plan snapshots**  
Any plan configuration can be saved as a versioned object. Snapshots capture all fields: tier structure, seat minimums, overage thresholds, billing anchor, and custom rate adjustments. Existing accounts referencing a plan version are never affected by changes made to newer versions.

**Staged migration**  
Accounts can be migrated from one plan version to another on a scheduled date (e.g. at next billing cycle boundary) or immediately. The migration preview shows the billing delta before commitment, including any prorated charges or credits.

**Grandfathering**  
When rolling out a new plan structure, existing accounts can be held on their current plan version indefinitely while new accounts onboard to the new version. Useful for enterprise customers mid-contract who are not yet ready to restructure.

**Rollback**  
If a migration causes unexpected billing behaviour, an account can be rolled back to its prior plan version within the same billing cycle. Rollback generates a corrective credit note automatically.

**Audit trail**  
Every plan version change — creation, account assignment, migration, rollback — is logged with timestamp, acting user, and before/after state. Available via the Maple dashboard and the API.

---

## Primary Use Case: Enterprise Plan Restructuring

Plan Versioning is most valuable when an enterprise customer needs to restructure their pricing arrangement mid-contract — typically when they are scaling beyond what their current plan structure was designed for.

**Common scenario:** A customer originally onboarded on a per-seat plan is growing their user base and transaction volume significantly. The economics of the per-seat model no longer reflect the value they receive. They want to restructure to a volume-tier plan where pricing scales with transaction volume rather than headcount.

Without Plan Versioning, this restructuring requires:
1. Cancelling the current subscription and all seat assignments
2. Creating a new subscription under the new plan
3. Manually reconciling billing history across the two subscriptions
4. Re-provisioning all seat assignments and API keys

With Plan Versioning, the migration is:
1. Define the new plan version (volume-tier structure, thresholds, rates)
2. Preview the billing impact for the account
3. Schedule the migration to the next billing cycle boundary
4. Existing seats, billing history, and API keys carry over automatically

---

## Prerequisites and Requirements

**Volume-tier pricing must be enabled** on the account before a migration to a volume-tier plan version can be scheduled. Contact the account team to confirm this is set up correctly in the Maple dashboard.

**Overage billing must be accurately configured.** Plan Versioning for volume-tier accounts relies on correct overage calculation — specifically, overages must be calculated against **net settled volume** (gross transaction volume minus refunds and chargebacks), not gross volume. Verify that the account's overage configuration is correct before scheduling a migration. Accounts migrated to volume-tier plans while overage billing is misconfigured may be overbilled until the configuration is corrected.

> **Note for Sales and CSM:** As of April 2026, there is an open engineering issue (ISS-032) affecting overage calculation accuracy for volume-tier accounts — overages are currently calculated on gross rather than net volume. This issue is in progress and expected to resolve in the near term. For accounts planning a Plan Versioning migration to a volume-tier structure, confirm ISS-032 is resolved before scheduling the migration to avoid overage overbilling.

**Minimum contract term:** Plan Versioning migrations are supported for accounts with at least 6 months remaining on their current contract, or at renewal. For accounts approaching renewal, the migration can be packaged as part of the renewal terms.

---

## How to Use Plan Versioning

**Via the Maple Dashboard:**  
Navigate to **Billing → Plans → Plan Versions**. Create a new version by duplicating an existing plan and modifying the configuration. Assign the new version to an account from the account detail page under **Billing → Plan Version**.

**Via the API:**  
```
POST /v1/plan-versions          # Create a new plan version
GET  /v1/plan-versions/{id}     # Retrieve a plan version
POST /v1/subscriptions/{id}/migrate-plan  # Schedule a migration
GET  /v1/subscriptions/{id}/migration-preview  # Preview billing impact
POST /v1/subscriptions/{id}/rollback-plan # Roll back to prior version
```

Full API reference available in the developer documentation.

---

## Sales Positioning

Plan Versioning removes the last major friction point in enterprise expansion deals where the customer needs to restructure their pricing arrangement. The historic objection — "we can't restructure mid-contract without breaking our billing history" — no longer applies.

Key talking points:
- **Zero disruption:** Seats, billing history, API keys, and webhooks all carry over. No re-provisioning required.
- **Finance-friendly:** Migration generates a clean transition invoice. No gap in billing periods. Audit trail satisfies enterprise finance requirements.
- **Reversible:** Rollback capability within the billing cycle removes the risk of committing to a new plan structure prematurely.
- **Expansion enabler:** For accounts on per-seat plans that are scaling on transaction volume, Plan Versioning is the bridge to a commercial arrangement that better reflects the value they receive from Maple.

---

## Related Articles

- ART-013: Creating Subscription Plans in Maple
- ART-015: How Maple Calculates Proration During Plan Changes
- ART-017: Usage-Based Billing in Maple
- ART-052: Maple Pricing and Plan Comparison
