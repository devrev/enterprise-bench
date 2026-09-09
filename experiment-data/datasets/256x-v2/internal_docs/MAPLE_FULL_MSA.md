# COMPARISON: Generated MSA vs. Actual Maple T&Cs

## Key Differences Identified

### 1. Company Name & Branding
- **Generated MSA:** "Maple Software Inc." (San Francisco, CA)
- **Actual T&C:** "Maple Payments International Limited" (Global/International entity)
- **Impact:** Need to align company name, jurisdiction, and international scope

### 2. Tone & Formality
- **Generated MSA:** More formal, enterprise SaaS-focused, detailed exhibits
- **Actual T&C:** More direct, payment processor-focused, merchant-oriented language
- **Impact:** Actual T&C is more prescriptive and protective of Maple's rights

### 3. Structure & Organization
- **Generated MSA:** 14 main sections + 2 exhibits (SLA, DPA)
- **Actual T&C:** 14 main sections, no separate exhibits (SLA embedded)
- **Impact:** Actual T&C integrates all terms in one document

### 4. Key Clauses Present in Actual T&C but Missing/Different in Generated MSA

#### A. Merchant Responsibilities (More Detailed)
- **Actual T&C:** Specific security breach notification requirements
- **Actual T&C:** Professional business conduct standards
- **Actual T&C:** Explicit customer service obligations

#### B. Payment Processing Terms (More Specific)
- **Actual T&C:** Authorization is on behalf of merchant (agency language)
- **Actual T&C:** Maple doesn't guarantee transaction approval
- **Actual T&C:** Transaction limits may change without notice
- **Generated MSA:** More generic service provision language

#### C. Fees (Different Structure)
- **Actual T&C:** Includes reserve fees, early termination fees, compliance fees
- **Actual T&C:** Explicit authorization to debit bank account for insufficient settlements
- **Generated MSA:** Simpler fee structure without merchant-specific penalties

#### D. Refund Policy (Separate Section)
- **Actual T&C:** Has entire section on refund obligations
- **Actual T&C:** Merchant must use same method as original transaction
- **Actual T&C:** Original processing fee not returned
- **Generated MSA:** Refund policy not explicitly addressed

#### E. Settlement Policy (Much More Detailed)
- **Actual T&C:** 
  - Standard settlement 2-5 business days
  - Rolling reserve with hold periods
  - Explicit right to delay/suspend settlement for risk
  - Can hold funds for 180 days post-termination
- **Generated MSA:** Basic payment terms without settlement mechanics

#### F. Chargeback Policy (Entirely Missing in Generated MSA)
- **Actual T&C:**
  - Merchant liable for all chargebacks + fees
  - Maple can debit account directly
  - Representment process outlined
  - Excessive chargeback ratio consequences (1% threshold)
  - Can result in fee increases, reserves, or termination

#### G. Prohibited Activities (More Extensive)
- **Actual T&C:** 
  - Very detailed list: adult content, gambling, crypto (unless approved)
  - Multi-level marketing, investment schemes
  - Transaction laundering/factoring
  - Payment aggregation without authorization
- **Generated MSA:** Generic prohibited activities clause

#### H. Limitation of Liability (Different Cap)
- **Actual T&C:** 3 months of fees
- **Generated MSA:** 12 months of fees
- **Impact:** Actual T&C more protective of Maple

#### I. Termination (More Aggressive)
- **Actual T&C:**
  - Maple can terminate immediately without notice
  - Can hold funds for 180 days post-termination
  - Explicit right to suspend for excessive chargebacks
- **Generated MSA:** 30-day cure period for breaches

#### J. Governing Law (International vs. California)
- **Actual T&C:** "Internationally recognized principles" + mutual arbitration
- **Generated MSA:** California law, San Francisco arbitration
- **Impact:** Actual T&C more flexible for global merchants

#### K. PCI DSS (Different Emphasis)
- **Actual T&C:** 
  - Merchant must comply with PCI DSS
  - Cannot store sensitive auth data (CVV, mag stripe, PIN)
  - Maple provides compliant solutions to minimize burden
- **Generated MSA:** Maple maintains PCI Level 1, less focus on merchant compliance

### 5. Key Clauses in Generated MSA Not in Actual T&C

#### A. Detailed SLA as Exhibit
- **Generated MSA:** Full SLA with uptime %, support tiers, credits
- **Actual T&C:** No formal SLA document
- **Impact:** Generated MSA more enterprise-friendly

#### B. Data Processing Addendum (DPA)
- **Generated MSA:** Separate DPA exhibit referenced
- **Actual T&C:** Basic data privacy section only
- **Impact:** Generated MSA more GDPR/CCPA compliant

#### C. Audit Rights
- **Generated MSA:** Customer can audit Maple (annually)
- **Actual T&C:** No audit rights mentioned
- **Impact:** Generated MSA more enterprise-friendly

#### D. Service Tier Differentiation
- **Generated MSA:** Starter/Growth/Enterprise tiers with different SLAs
- **Actual T&C:** No tier differentiation, uniform terms
- **Impact:** Actual T&C treats all merchants equally

---

## Recommendations for Aligned MSA

### Option 1: Keep Generated MSA (Enterprise SaaS Model)
**Use when:**
- Customer is large enterprise
- Requires formal SLA commitments
- Needs GDPR/CCPA DPA
- Expects audit rights
- Multi-year contract with committed spend

**Pros:** Enterprise-friendly, detailed protections, clear SLA metrics  
**Cons:** Doesn't match actual Maple T&C, may confuse existing customers

---

### Option 2: Align with Actual T&C (Payment Processor Model)
**Use when:**
- Customer is typical merchant (SMB to mid-market)
- Standard payment processing relationship
- Needs to match Maple's actual legal terms
- Consistency with other Maple customers

**Pros:** Matches actual legal framework, realistic payment processor terms  
**Cons:** Less merchant-friendly, no formal SLA, aggressive termination rights

---

### Option 3: Hybrid Model (Recommended)
**Combine best of both:**
- Use actual T&C as base structure and language
- Add SLA as optional exhibit for Enterprise tier
- Keep DPA for GDPR/CCPA compliance
- Maintain service tier differentiation
- Add enterprise-friendly provisions (audit rights, longer cure periods)

**Pros:** Legally accurate + enterprise accommodations  
**Cons:** More complex, need to manage two contract types

---

# REWRITTEN MSA: Aligned with Actual Maple T&Cs

Below is an MSA that aligns with the actual Maple T&C structure while incorporating the SLA elements we created.

---

# MASTER SERVICES AGREEMENT

**Between:**

**Maple Payments International Limited** ("Maple", "we", "us", or "our")  
Global Payment Solutions Provider  
Email: legal@maplepayments.com  
Website: www.maplepayments.com

**AND**

**[Customer Legal Name]** ("Merchant", "Customer", "you", or "your")  
[Customer Address]

**Effective Date:** [Date]  
**Agreement ID:** [Contract Number]

---

## 1. INTRODUCTION AND SCOPE

### 1.1 Agreement Purpose

This Master Services Agreement ("Agreement") constitutes a legally binding agreement between Maple Payments International Limited and you governing your use of Maple's payment processing services, gateway services, and related payment solutions. By registering for, accessing, or using our Services, you acknowledge that you have read, understood, and agree to be bound by this Agreement.

### 1.2 Services Provided

Maple provides global payment gateway services, merchant account services, and related payment processing solutions to facilitate electronic transactions between merchants and their customers worldwide. Our Services enable you to accept credit cards, debit cards, and other payment methods through online, mobile, and point-of-sale channels.

### 1.3 Service Tier

Your specific Service Tier (Starter, Growth, or Enterprise) is specified in the Order Form and determines your applicable fees, support levels, and SLA commitments as set forth in the Service Level Agreement attached as Schedule A.

### 1.4 Entire Agreement

This Agreement, together with the Order Form, Service Level Agreement (Schedule A), and any amendments, constitutes the entire agreement between the parties and supersedes all prior agreements and understandings, whether written or oral, regarding the subject matter herein.

---

## 2. DEFINITIONS

For purposes of this Agreement, the following terms shall have the meanings set forth below:

- **"Cardholder"** means an individual who holds a valid payment card issued by a card-issuing financial institution.
- **"Chargeback"** means a transaction reversal initiated by the cardholder's issuing bank to dispute a transaction.
- **"Processing Fee"** means the fee charged by Maple for each successful transaction, calculated as a percentage of the transaction amount and/or fixed fee per transaction.
- **"Merchant Account"** means the account established for you to receive settlement of funds from transactions processed through our Services.
- **"Payment Networks"** means Visa, Mastercard, American Express, Discover, and other card networks and payment methods supported by Maple.
- **"Services"** means all payment processing, gateway, and related services provided by Maple as described in this Agreement.
- **"Settlement"** means the transfer of funds from processed transactions to your designated bank account.
- **"Transaction"** means any purchase, refund, authorization, or other payment operation processed through Maple's platform.
- **"Service Tier"** means the applicable service level (Starter, Growth, or Enterprise) as specified in the Order Form.
- **"SLA"** means the Service Level Agreement set forth in Schedule A defining uptime commitments, support response times, and service credits.

---

## 3. MERCHANT RESPONSIBILITIES

### 3.1 Eligibility and Registration

You represent and warrant that you are a legally established business entity with the authority to enter into this Agreement. You must provide accurate, complete, and current information during registration and maintain the accuracy of such information throughout the term of this Agreement. You will promptly notify Maple of any changes to your business structure, ownership, operations, or contact information.

### 3.2 Compliance with Laws

You agree to comply with all applicable laws, rules, and regulations in your jurisdiction and in the jurisdictions where your customers are located, including but not limited to those governing:
- Electronic transactions and e-commerce
- Consumer protection and unfair business practices
- Data privacy and protection (including GDPR, CCPA, and other applicable privacy laws)
- Anti-money laundering (AML) and counter-terrorism financing (CTF)
- Know Your Customer (KYC) requirements
- Economic sanctions and export controls

You shall obtain and maintain all necessary licenses, permits, and registrations required to conduct your business and use the Services.

### 3.3 Business Operations

You must:
- Conduct your business in a professional manner consistent with industry standards
- Provide clear, accurate descriptions of goods and services offered
- Honor all sales commitments and warranties
- Maintain reasonable customer service standards
- Respond promptly to customer inquiries and complaints
- Process customer refunds and returns in accordance with your stated policies
- Deliver goods and services as promised

You are solely responsible for all aspects of your business operations, including pricing, product quality, fulfillment, delivery, and customer support.

### 3.4 Security Requirements

You must implement and maintain reasonable security measures to protect:
- Transaction data and payment information
- Customer personal information
- Access credentials (usernames, passwords, API keys)
- Your systems and networks from unauthorized access

You shall **immediately notify Maple** within 24 hours of any:
- Suspected or actual security breach or data compromise
- Unauthorized access to your Maple account or API credentials
- Loss or theft of devices containing sensitive data
- Any circumstances that may affect the security of Customer Data or payment information

---

## 4. PAYMENT PROCESSING TERMS

### 4.1 Authorization to Process

You authorize Maple to process payment transactions on your behalf as your agent in accordance with this Agreement. Maple will use commercially reasonable efforts to process transactions submitted by you, but **does not guarantee approval of any particular transaction**. 

Authorization and decline decisions are made by:
- Card-issuing banks (for credit and debit card transactions)
- Payment networks (Visa, Mastercard, American Express, Discover)
- Risk management systems and fraud detection services

Maple is not responsible for transactions declined by issuing banks or payment networks.

### 4.2 Processing Standards

All transactions must comply with Payment Network rules and regulations, including Visa Core Rules, Mastercard Rules, and applicable American Express and Discover operating regulations.

Maple reserves the right to decline, block, or reverse any transaction that:
- Appears suspicious, fraudulent, or unauthorized
- Violates applicable laws, regulations, or Payment Network rules
- Violates this Agreement or Maple's Acceptable Use Policy
- Exposes Maple to legal, financial, or reputational risk

You shall not knowingly process or attempt to process transactions that are fraudulent, unauthorized, or violate applicable laws or Payment Network rules.

### 4.3 Transaction Limits and Monitoring

Maple may impose transaction limits based on:
- Your business profile and industry classification
- Transaction history and processing patterns
- Risk assessment and fraud indicators
- Chargeback ratios and dispute rates
- Regulatory requirements and Payment Network rules

These limits may include:
- Maximum transaction amount per transaction
- Maximum daily, weekly, or monthly processing volume
- Maximum number of transactions per time period
- Restrictions on certain card types or geographic regions

Maple may modify these limits at any time **with or without notice** to manage risk, ensure compliance with regulatory requirements, and maintain the integrity of the payment system.

---

## 5. FEES AND CHARGES

### 5.1 Processing Fees

You agree to pay Maple processing fees for each successful transaction processed through our Services. The applicable fee rates are set forth in the Order Form and Schedule A (Service Level Agreement) and may vary based on:
- Service Tier (Starter, Growth, or Enterprise)
- Transaction type (card-present, card-not-present, recurring)
- Payment method (credit card, debit card, ACH, alternative payments)
- Card type (consumer vs. commercial, rewards cards)
- Geographic location of the cardholder and transaction
- Transaction volume and monthly processing levels

### 5.2 Additional Fees

In addition to processing fees, you may be charged the following fees as applicable:

**Platform and Service Fees:**
- Monthly or annual subscription fees based on Service Tier
- Setup and onboarding fees (if applicable)
- Integration and technical support fees (beyond standard support)

**Transaction-Related Fees:**
- Chargeback handling fees ($15-$25 per chargeback)
- Refund processing fees (if applicable per your Service Tier)
- Failed transaction or decline fees (if applicable)
- Currency conversion fees for multi-currency transactions
- Cross-border processing fees for international transactions

**Risk Management Fees:**
- Rolling reserve fees (percentage of transaction volume held in reserve)
- Security deposit or collateral requirements
- Enhanced monitoring and compliance fees

**Account Management Fees:**
- Early termination fees (if terminating before end of contract term)
- Inactivity fees (if account inactive for extended period)
- Statement and reporting fees (for custom reports beyond standard)
- Compliance, regulatory, and audit fees

All applicable fees will be disclosed in your Order Form, merchant dashboard, or invoice.

### 5.3 Taxes and Duties

All fees are **exclusive** of applicable taxes, duties, levies, or governmental charges imposed by any jurisdiction, including:
- Sales tax, use tax, or value-added tax (VAT)
- Goods and services tax (GST)
- Withholding taxes
- Other indirect taxes or government fees

You are responsible for payment of all applicable taxes related to your use of Maple's Services. Where Maple is required by law to collect or remit taxes on your behalf, such amounts shall be added to your invoice and you shall pay the full amount including taxes.

### 5.4 Fee Deduction and Payment

**Automatic Deduction:** Maple is authorized to automatically deduct all applicable fees, charges, and taxes from transaction settlement amounts before transferring funds to your designated bank account.

**Insufficient Settlement Amounts:** In cases where settlement amounts are insufficient to cover fees owed (for example, due to refunds, chargebacks, or low transaction volume), you authorize Maple to:
- Debit your designated bank account for the outstanding balance
- Offset amounts owed against future settlements
- Bill you separately via invoice (due within the payment terms specified)

**Late Payment:** If fees remain unpaid beyond the due date, you agree to pay:
- Late payment fees of $50 or 5% of the outstanding balance, whichever is greater
- Interest on overdue amounts at the rate of 1.5% per month (18% annually) or the maximum rate permitted by law, whichever is less

### 5.5 Fee Adjustments

Maple reserves the right to adjust fees with **sixty (60) days' prior written notice** for Starter and Growth tier customers. Enterprise customers are subject to the pricing terms negotiated in their Order Form for the duration of the then-current contract term, subject to annual CPI adjustments if specified in the Order Form.

---

## 6. REFUND POLICY

### 6.1 Merchant Refund Obligations

You are responsible for establishing and clearly communicating your refund and return policy to customers. Your refund policy must:
- Comply with all applicable consumer protection laws in your jurisdiction
- Be displayed prominently on your website, checkout pages, and receipts
- Be no less favorable than policies you apply to transactions processed through other payment channels
- Include timeframes for refund requests and processing

### 6.2 Processing Refunds

All refunds must be processed through Maple's platform to the **original payment method** used for the transaction. You shall not:
- Provide refunds in cash or store credit (unless legally required)
- Process refunds through alternative payment methods
- Require customers to accept refunds in a different form than the original payment

**Processing Fees:** Maple does not charge an additional fee to process refunds. However, the **original processing fee is not refunded** to you when you issue a refund to a customer. This is standard in the payment processing industry.

### 6.3 Refund Timeframe

Refunds typically process within **5-10 business days** from the date you submit the refund request through Maple's platform. However, the actual time for funds to appear in the customer's account may vary depending on:
- The card-issuing bank's processing times
- Payment method used (credit card refunds typically faster than debit card refunds)
- Geographic location and banking system

You must process refund requests **promptly** and within the timeframe specified in your refund policy and required by applicable consumer protection laws.

### 6.4 Refund Documentation

You must maintain documentation supporting all refund requests, including:
- Customer refund request (email, support ticket, etc.)
- Reason for refund
- Date refund was issued
- Confirmation that refund was processed to original payment method

This documentation must be available for review by Maple or Payment Networks if disputes arise.

---

## 7. SETTLEMENT POLICY

### 7.1 Settlement Cycle

Funds from successful transactions will be settled (transferred) to your designated bank account according to the settlement cycle specified in the Order Form and determined by:
- Your Service Tier (Starter, Growth, or Enterprise)
- Business risk profile and transaction history
- Geographic region and payment methods accepted
- Account age and processing history with Maple

**Standard Settlement Cycles:**
- **Starter Tier:** T+5 business days (5 business days after transaction date)
- **Growth Tier:** T+3 business days (3 business days after transaction date)
- **Enterprise Tier:** T+2 business days (2 business days after transaction date)

Faster settlement may be available based on risk assessment and processing history.

### 7.2 Settlement Currency and Bank Account

Settlement will be made in the currency specified in your Order Form to the bank account you designate during onboarding. You are responsible for:
- Providing accurate bank account information
- Ensuring the account remains active and in good standing
- Notifying Maple immediately of any changes to your bank account

If settlement fails due to invalid or closed bank account information, Maple may hold funds and charge an administrative fee for re-processing.

### 7.3 Rolling Reserve

Maple may establish a **rolling reserve account** and withhold a percentage of transaction proceeds to cover potential:
- Chargebacks and disputes
- Refunds and returns
- Other liabilities or obligations under this Agreement

**Reserve Terms:**
- **Reserve Percentage:** Determined based on your business risk profile (typically 5%-20% of transaction volume)
- **Reserve Period:** Funds held for 90-180 days before release
- **Release Schedule:** Released on a rolling basis (funds from Day 1 released after 90-180 days)
- **Reserve Adjustments:** Maple may increase or decrease reserve requirements based on changes in chargeback ratios, transaction patterns, or risk assessment

Reserve requirements will be specified in your Order Form or communicated via your merchant dashboard.

### 7.4 Settlement Delays and Suspension

Maple reserves the right to **delay or suspend settlement** if:

**(a) Suspicious Activity:** We detect suspicious, potentially fraudulent, or high-risk activity in your account

**(b) Excessive Chargebacks:** You receive an excessive number of chargebacks or customer complaints (typically exceeding 1% of transaction count or volume)

**(c) Breach of Agreement:** You breach any material term of this Agreement, including prohibited activities or compliance violations

**(d) Legal Requirements:** We are required to delay settlement by law, regulation, court order, or Payment Network rules

**(e) Risk to Maple:** Continuation of normal settlement would expose Maple to legal, financial, or reputational risk

**(f) Pending Investigation:** An investigation is ongoing regarding your account, transactions, or business practices

**Notice:** Maple will make reasonable efforts to notify you of settlement delays or suspensions, except where doing so would:
- Compromise an investigation
- Violate legal obligations (such as suspicious activity reporting requirements)
- Alert you to fraud detection measures

### 7.5 Post-Termination Settlement

Upon termination of this Agreement, Maple may hold funds for up to **one hundred eighty (180) days** to cover potential chargebacks, refunds, and other liabilities. After this period, remaining funds (if any) will be settled to your designated bank account, less any fees, chargebacks, or reserves owed.

---

## 8. CHARGEBACK POLICY

### 8.1 Chargeback Liability

**You are fully liable for all chargebacks** and associated fees, regardless of the reason for the chargeback or its validity. When a chargeback occurs, Maple will:
1. **Debit the transaction amount** from your settlement or directly from your designated bank account
2. **Debit chargeback fees** ($15-$25 per chargeback as specified in your Order Form)
3. **Notify you** of the chargeback and provide details of the dispute

You bear **full financial responsibility** for chargebacks even if:
- The chargeback is later reversed in your favor
- The transaction was authorized by the cardholder
- You provided the goods or services as described
- The customer is acting in bad faith

This is standard in the payment processing industry due to Payment Network rules.

### 8.2 Chargeback Representment

You have the right to **dispute chargebacks** by providing compelling evidence that the transaction was valid, authorized, and fulfilled as agreed. This process is called "representment."

**Maple's Role:**
- We will assist in the representment process according to Payment Network rules
- We will submit your evidence to the issuing bank and Payment Networks
- We **cannot guarantee a favorable outcome** - the issuing bank makes the final decision
- We will communicate the outcome and any further actions required

**Your Responsibilities:**
- Submit representment documentation within the timeframe specified by Payment Networks (typically 7-10 days from chargeback notification)
- Provide compelling evidence, including:
  - Proof of delivery (tracking numbers, signed delivery receipts)
  - Proof of service (account logs, usage records)
  - Customer communications (emails, chat logs)
  - Authorization records (AVS/CVV match, IP address, device fingerprint)
  - Terms and conditions acceptance records
  - Refund policy documentation

**Representment Fees:** Maple does not charge additional fees for standard representment assistance. However, fees may apply for expedited handling or extensive documentation preparation.

### 8.3 Excessive Chargebacks

Payment Networks and card brands impose strict limits on acceptable chargeback ratios. If your chargeback ratio exceeds industry standards (typically **1% of transaction count or volume**), Maple may:

**(a) Increase Processing Fees:** Add a high-risk processing surcharge or increase your standard processing rates

**(b) Increase Reserve Requirements:** Increase rolling reserve percentage or extend reserve hold period

**(c) Impose Monitoring Programs:** Require you to participate in Visa's Chargeback Monitoring Program (VCMP), Mastercard's Excessive Chargeback Merchant (ECM) program, or similar programs

**(d) Suspend Processing:** Temporarily suspend your ability to process new transactions until chargeback issues are resolved

**(e) Terminate Agreement:** Terminate this Agreement immediately if chargeback ratios remain excessive or indicate fraudulent activity

**Payment Network Fines:** In addition to Maple's actions, Payment Networks may impose fines and penalties ranging from $5,000 to $100,000+ for merchants with excessive chargebacks. You are responsible for payment of all such fines.

### 8.4 Chargeback Monitoring and Reporting

Maple provides chargeback monitoring and reporting through your merchant dashboard, including:
- Real-time chargeback notifications
- Monthly chargeback ratio calculations
- Chargeback reason code analysis
- Alerts when approaching Payment Network thresholds

You are responsible for monitoring your chargeback ratios and taking proactive steps to reduce chargebacks.

---

## 9. DATA PRIVACY AND SECURITY

### 9.1 Data Protection Compliance

Both parties agree to comply with all applicable data protection and privacy laws and regulations in the jurisdictions where they operate, including:
- General Data Protection Regulation (GDPR) - European Union
- California Consumer Privacy Act (CCPA) - United States
- Other applicable data protection and privacy laws

Each party shall implement appropriate technical and organizational measures to protect personal data and sensitive information from unauthorized access, disclosure, alteration, or destruction.

**Data Processing Addendum:** If you are subject to GDPR or similar data protection laws requiring a formal Data Processing Agreement, the Data Processing Addendum (Schedule B) applies and forms part of this Agreement.

### 9.2 Payment Card Industry Data Security Standard (PCI DSS)

You must comply with the **Payment Card Industry Data Security Standard (PCI DSS)** requirements applicable to your business based on your transaction volume and integration method.

**Prohibited Storage:** You shall **not store** the following sensitive authentication data under any circumstances:
- Full magnetic stripe data from the back of cards
- Card verification codes (CVV2, CVC2, CID)
- Personal identification numbers (PINs) or PIN blocks

Violation of these prohibitions may result in immediate termination and significant fines from Payment Networks.

**Compliance Assistance:** Maple provides PCI-compliant payment processing solutions including tokenization, hosted payment pages, and secure APIs designed to minimize your PCI DSS compliance burden. By using these solutions, you reduce your PCI DSS scope.

**Self-Assessment:** Depending on your transaction volume, you may be required to complete an annual PCI DSS Self-Assessment Questionnaire (SAQ) and Attestation of Compliance (AOC). Maple will provide guidance on which SAQ level applies to your integration.

### 9.3 Data Usage and Analytics

Maple may collect, process, and analyze transaction data and other information for purposes of:
- Providing the Services and processing transactions
- Preventing fraud and managing risk
- Improving products, services, and user experience
- Complying with legal and regulatory obligations
- Generating aggregated and anonymized analytics and benchmarks

**No Sale of Customer Data:** Maple will not sell or rent your customer data to third parties for marketing purposes without your explicit consent.

**Aggregated Data:** Maple may use aggregated and anonymized data (where individual merchants and customers cannot be identified) for analytics, benchmarking, research, and product improvement purposes.

### 9.4 Data Retention

Transaction records and related data will be retained for the period required by:
- Applicable laws and regulations (typically 7-10 years for financial records)
- Payment Network rules (typically 18 months minimum)
- Business needs for dispute resolution and compliance

Upon termination of this Agreement, Maple will:
- Retain data as necessary to comply with legal obligations
- Retain data necessary to resolve disputes, chargebacks, and claims
- Make transaction data available to you for export for thirty (30) days after termination (unless prohibited by law)
- Delete or anonymize data no longer required for legal or business purposes in accordance with Maple's data retention policies

---

## 10. PROHIBITED ACTIVITIES

You shall not use Maple's Services for any of the following prohibited activities:

### 10.1 Illegal or Restricted Goods and Services
- Sale of illegal goods or services, including but not limited to controlled substances, narcotics, or drug paraphernalia
- Weapons, firearms, ammunition, or explosives (unless properly licensed and approved by Maple)
- Counterfeit, stolen, or fraudulently obtained goods
- Goods or services that violate intellectual property rights

### 10.2 Regulated Industries (Require Prior Approval)
- Adult content, sexually oriented materials, or escort services (unless specifically approved by Maple in writing)
- Gambling, betting, lottery, sweepstakes, or games of chance (unless properly licensed and approved by Maple)
- Cryptocurrency exchanges, trading platforms, or token sales (unless specifically approved by Maple)
- High-risk financial services including payday loans, debt consolidation, credit repair

### 10.3 Fraudulent or Deceptive Practices
- Fraudulent, deceptive, or misleading business practices
- Bait-and-switch schemes or false advertising
- Pyramid schemes, multi-level marketing schemes, or "get rich quick" programs
- Investment opportunities, securities, or commodities trading (unless properly licensed)
- Work-from-home programs, business opportunities, or envelope stuffing schemes

### 10.4 Financial Crimes
- Money laundering, terrorist financing, or other financial crimes
- Transactions violating economic sanctions or export control laws
- Structuring transactions to evade reporting requirements
- Processing transactions for entities on sanctions lists (OFAC, UN, EU, etc.)

### 10.5 Payment Processing Violations
- Processing transactions for third parties without authorization (payment aggregation or factoring)
- Transaction laundering (processing payments on behalf of undisclosed merchants)
- Factoring or purchasing payment card receivables from other merchants
- Processing transactions after account termination or suspension

### 10.6 Abusive or Illegal Conduct
- Abusive, harassing, discriminatory, or threatening conduct toward customers or Maple staff
- Violations of consumer protection laws or unfair business practices
- Violations of privacy laws or unauthorized collection/use of personal data
- Activities that violate Payment Network rules or operating regulations

### 10.7 Other Prohibited Activities
- Sale of tobacco or tobacco products (unless properly licensed and age-verified)
- Sale of prescription pharmaceuticals or controlled medical devices (unless properly licensed)
- Telemarketing or outbound sales (unless compliant with TCPA and approved by Maple)
- Negative option billing or continuity subscriptions without clear disclosures
- Any activity prohibited by applicable laws, Payment Network rules, or card brand policies

**Maple's Rights:** Maple reserves the right to expand this list of prohibited activities at any time without prior notice. Engaging in prohibited activities may result in:
- Immediate suspension or termination of your account
- Withholding of settlements to cover potential losses
- Reporting to law enforcement, regulatory authorities, or Payment Networks
- Legal action to recover damages

If you are uncertain whether your business activities are permitted, contact Maple's compliance team at compliance@maplepayments.com **before** processing transactions.

---

## 11. SERVICE LEVEL AGREEMENT (SLA)

The Service Level Agreement defining platform uptime commitments, support response times, resolution targets, and service credit remedies is set forth in **Schedule A: Service Level Agreement** attached to this Agreement and incorporated by reference.

Key SLA metrics vary by Service Tier:

**Uptime Commitments:**
- Starter: 99.5% monthly uptime
- Growth: 99.5% monthly uptime
- Enterprise: 99.95% monthly uptime

**Support Response Times:**
- Vary by priority level (P0/P1/P2/P3) and Service Tier
- Enterprise customers receive 24/7 support for P0 and P1 issues
- Starter and Growth customers receive business hours support

**Service Credits:**
- Available if Maple fails to meet uptime or response time commitments
- Credits are your sole remedy for SLA breaches
- Must be requested within 60 days of the affected service period

For complete SLA terms, see Schedule A.

---

## 12. LIMITATION OF LIABILITY

### 12.1 Service Availability

While Maple strives to provide uninterrupted service, we **do not guarantee** that our Services will be available at all times or free from errors, delays, or interruptions. 

Maple shall not be liable for service interruptions, delays, or failures caused by circumstances beyond our reasonable control, including:
- Internet service provider outages or telecommunications failures
- Power failures or utility disruptions
- Natural disasters, severe weather, or acts of God
- Acts of war, terrorism, riots, or civil unrest
- Governmental actions, laws, or regulations
- Pandemics or public health emergencies
- Failures of third-party service providers (including Payment Networks, card-issuing banks, or cloud infrastructure providers)

### 12.2 Disclaimer of Warranties

**AS-IS BASIS:** Maple's Services are provided **"AS IS"** and **"AS AVAILABLE"** without warranties of any kind, whether express or implied.

**DISCLAIMER:** To the maximum extent permitted by applicable law, Maple disclaims all warranties, including but not limited to:
- Implied warranties of **merchantability**
- Implied warranties of **fitness for a particular purpose**
- Implied warranties of **non-infringement**
- Warranties of **title**
- Warranties arising from **course of dealing or usage of trade**

**NO GUARANTEE:** Maple does not warrant that:
- The Services will meet your specific requirements or expectations
- The Services will operate uninterrupted, error-free, or secure
- The results obtained from use of the Services will be accurate or reliable
- Any errors or defects in the Services will be corrected
- Your use of the Services will not result in losses or liabilities

### 12.3 Liability Cap

To the maximum extent permitted by applicable law, Maple's **total cumulative liability** for any and all claims arising out of or related to this Agreement, whether in contract, tort (including negligence), strict liability, or otherwise, shall **not exceed** the total fees paid by you to Maple in the **three (3) months** immediately preceding the event giving rise to the claim.

This limitation applies **regardless of**:
- The legal theory upon which the claim is based
- Whether Maple has been advised of the possibility of such damages
- Whether the limited remedy fails of its essential purpose

### 12.4 Exclusion of Consequential Damages

**TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, IN NO EVENT SHALL MAPLE BE LIABLE FOR ANY:**

- **Indirect damages** (damages not directly caused by Maple's actions)
- **Incidental damages** (damages incidental to Maple's breach)
- **Special damages** (damages arising from special circumstances)
- **Consequential damages** (damages resulting as a consequence of the breach)
- **Punitive or exemplary damages** (damages intended to punish)

**Including but not limited to:**
- Loss of profits, revenue, or income
- Loss of business opportunities or contracts
- Loss of anticipated savings
- Loss of data or information
- Loss of goodwill or reputation
- Business interruption or downtime
- Cost of procuring substitute services

**This exclusion applies even if Maple has been advised of the possibility of such damages.**

### 12.5 Exceptions to Limitations

The limitations in Sections 12.3 and 12.4 **do not apply** to:
- Your payment obligations for fees and charges under this Agreement
- Your indemnification obligations under Section 12.6
- Claims arising from your gross negligence, willful misconduct, or fraud
- Claims arising from your violation of applicable laws or regulations
- Claims arising from your breach of confidentiality obligations
- Claims arising from your infringement of Maple's intellectual property rights
- Any liabilities that cannot be limited or excluded under applicable law

### 12.6 Indemnification

**Your Indemnification of Maple:**

You agree to indemnify, defend, and hold harmless Maple, its affiliates, subsidiaries, officers, directors, employees, agents, and service providers from and against any and all claims, damages, losses, liabilities, costs, and expenses (including reasonable attorneys' fees and court costs) arising from or related to:

**(a)** Your breach of this Agreement or violation of any term or condition

**(b)** Your violation of any applicable law, regulation, or Payment Network rule

**(c)** Your products, services, or business operations

**(d)** Disputes with your customers, including chargebacks, refunds, and complaints

**(e)** Infringement or alleged infringement of intellectual property rights of third parties

**(f)** Your negligence, willful misconduct, or fraud

**(g)** Any third-party claims related to your use of the Services or your business activities

**(h)** Your failure to comply with PCI DSS requirements or data security obligations

**Maple's Indemnification of You (Enterprise Tier Only):**

For Enterprise tier customers only, Maple agrees to indemnify, defend, and hold you harmless from third-party claims that Maple's Services infringe such third party's intellectual property rights, provided that:
- You promptly notify Maple in writing of the claim
- You grant Maple sole control of the defense and settlement
- You reasonably cooperate with Maple in the defense

This indemnification does not apply if the infringement arises from your modification of the Services or use of the Services in combination with third-party products or services.

**Indemnification Procedure:**

The indemnified party will:
- Provide the indemnifying party with prompt written notice of any claim
- Provide reasonable cooperation in the defense of the claim
- Not settle or compromise the claim without the indemnifying party's prior written consent

The indemnifying party will have sole control of the defense and settlement, provided that settlements do not admit liability on behalf of the indemnified party or impose obligations on the indemnified party without the indemnified party's consent.

---

## 13. TERM AND TERMINATION

### 13.1 Term

This Agreement commences on the Effective Date and continues for the **initial term** specified in the Order Form (typically 12, 24, or 36 months for committed contracts, or month-to-month for non-committed agreements).

### 13.2 Renewal

Unless either party provides written notice of non-renewal at least **sixty (60) days** before the end of the then-current term, this Agreement will automatically renew for successive periods equal to the initial term (or month-to-month for non-committed agreements).

### 13.3 Termination by Merchant

**For Committed Contracts:** You may terminate this Agreement before the end of the contract term by providing **thirty (30) days' written notice** to Maple. You remain liable for:
- All fees, chargebacks, and obligations incurred prior to termination
- Early termination fees as specified in the Order Form (typically 25%-50% of remaining contract value)
- All outstanding invoices and amounts owed

**For Month-to-Month Agreements:** You may terminate this Agreement at any time by providing **thirty (30) days' written notice** to Maple without early termination fees.

### 13.4 Termination by Maple

**Immediate Termination Without Notice:**

Maple may terminate or suspend this Agreement and your access to the Services **immediately without notice** if:

**(a) Material Breach:** You breach any material term of this Agreement and fail to cure within seven (7) days of written notice (or immediately for breaches involving fraud, illegal activity, or security violations)

**(b) Fraudulent Activity:** You engage in fraudulent, illegal, or prohibited activities

**(c) Excessive Chargebacks:** Your chargeback ratio exceeds acceptable levels (typically 1% of transaction count or volume) or indicates fraudulent activity

**(d) Insolvency:** You become insolvent, make an assignment for the benefit of creditors, or become subject to bankruptcy, receivership, or similar proceedings

**(e) Legal or Financial Risk:** Continuation of Services would violate applicable laws, Payment Network rules, or expose Maple to legal, financial, or reputational risk

**(f) Regulatory Requirement:** Termination is required by regulatory authorities, Payment Networks, or law enforcement

**(g) Prohibited Activities:** You engage in activities listed in Section 10 (Prohibited Activities)

**(h) False Information:** You provided false, misleading, or incomplete information during registration or at any time during the term

**Termination With Notice:**

Maple may terminate this Agreement with **thirty (30) days' written notice** for any reason or no reason, including:
- Changes in Maple's business strategy or service offerings
- Non-renewal of agreements with Payment Networks or acquiring banks
- Economic or business reasons

### 13.5 Effects of Termination

**Immediate Effects:**

Upon termination or suspension:
- Maple will cease processing new transactions immediately
- Your access to the merchant dashboard and API will be disabled
- All outstanding invoices become immediately due and payable

**Settlement of Remaining Funds:**

Maple will settle remaining funds subject to:
- Deduction of all fees, charges, and taxes owed
- Deduction of all chargebacks received (including those received post-termination)
- Holding reserves to cover potential future chargebacks and liabilities
- Compliance with Payment Network rules and legal requirements

**Reserve Hold Period:**

Maple may hold funds for up to **one hundred eighty (180) days** following termination to cover:
- Chargebacks that may be filed within Payment Network timeframes (typically 120-180 days)
- Refunds, returns, and customer disputes
- Fines, penalties, or fees imposed by Payment Networks
- Any other liabilities or obligations under this Agreement

After the hold period, remaining funds (if any) will be settled to your designated bank account, less any fees, chargebacks, or reserves owed.

**No Refunds:**

Termination does not entitle you to any refund of fees paid prior to termination, including monthly/annual subscription fees.

### 13.6 Survival of Terms

The following sections survive termination of this Agreement:
- Section 5 (Fees and Charges) - for amounts owed
- Section 7 (Settlement Policy) - for post-termination settlement
- Section 8 (Chargeback Policy) - for post-termination chargebacks
- Section 9 (Data Privacy and Security) - for data retention obligations
- Section 11 (Limitation of Liability) - limitation and disclaimer provisions
- Section 12.6 (Indemnification) - indemnification obligations
- Section 14 (Governing Law and Dispute Resolution) - for resolution of disputes arising from termination
- Any other provision that by its nature should survive termination

---

## 14. GOVERNING LAW AND DISPUTE RESOLUTION

### 14.1 Governing Law

This Agreement shall be governed by and construed in accordance with **internationally recognized principles of contract law and commercial practices**, including the United Nations Convention on Contracts for the International Sale of Goods (CISG) where applicable.

The parties agree to interpret this Agreement in good faith and in accordance with its plain meaning, giving due consideration to commercial reasonableness and industry practices in the payment processing sector.

### 14.2 Informal Dispute Resolution

Before initiating any formal dispute resolution proceedings, the parties agree to attempt to resolve any dispute, claim, or controversy arising out of or relating to this Agreement through **good faith negotiations** for a period of **thirty (30) days**.

Negotiations shall be between executives or senior managers with authority to settle the dispute. Either party may initiate this process by sending written notice to the other party at the address specified in this Agreement or the Order Form.

### 14.3 Binding Arbitration

If the parties are unable to resolve the dispute through informal negotiations within thirty (30) days, the dispute shall be resolved through **binding arbitration** administered by a mutually agreed international arbitration institution, such as:
- International Chamber of Commerce (ICC)
- London Court of International Arbitration (LCIA)
- American Arbitration Association (AAA) - International Centre for Dispute Resolution (ICDR)
- Singapore International Arbitration Centre (SIAC)

**Arbitration Procedures:**

- **Arbitrator Selection:** The arbitration shall be conducted by a single arbitrator mutually agreed upon by the parties within thirty (30) days of the initiation of arbitration. If the parties cannot agree on an arbitrator, the arbitrator shall be appointed by the arbitration institution in accordance with its rules.

- **Language:** The arbitration shall be conducted in **English**.

- **Place of Arbitration:** The place (legal seat) of arbitration shall be mutually agreed by the parties within thirty (30) days of the initiation of arbitration. If the parties cannot agree, the place of arbitration shall be determined by the arbitrator based on considerations of convenience, neutrality, and enforceability.

- **Applicable Rules:** The arbitration shall be conducted in accordance with the rules of the chosen arbitration institution in effect at the time of the arbitration, except as modified by this Agreement.

- **Discovery:** Discovery shall be limited to the exchange of documents and written questions, with no depositions, unless the arbitrator determines that additional discovery is necessary for a fair resolution.

- **Confidentiality:** The arbitration proceedings, including all submissions, evidence, and the arbitrator's award, shall be kept confidential by the parties, except as required by law or to enforce the award.

- **Final and Binding:** The arbitrator's decision shall be final and binding on the parties, and judgment upon the award may be entered in any court of competent jurisdiction.

**Costs and Fees:**

Each party shall bear its own attorneys' fees and costs, unless the arbitrator determines that one party should bear some or all of the other party's costs based on the outcome of the arbitration and the parties' conduct during the proceedings.

The parties shall share equally the fees and expenses of the arbitrator and the arbitration institution, unless the arbitrator determines otherwise.

### 14.4 Exceptions to Arbitration

Notwithstanding the arbitration provision in Section 14.3, either party may seek the following remedies in a court of competent jurisdiction **without first pursuing arbitration**:

**(a) Injunctive Relief:** Either party may seek temporary or preliminary injunctive relief or other equitable remedies to:
- Protect intellectual property rights (trademarks, copyrights, trade secrets, patents)
- Prevent irreparable harm or ongoing breaches
- Enforce confidentiality obligations
- Prevent disclosure or misuse of Confidential Information

**(b) Small Claims Court:** Either party may bring an individual action in small claims court for claims within the court's jurisdiction, provided the action remains in small claims court and is not removed or appealed to a court of general jurisdiction.

**(c) Provisional Remedies:** Either party may seek provisional remedies (such as attachment, garnishment, or preliminary injunctions) in aid of arbitration or to preserve the status quo pending arbitration.

### 14.5 Class Action Waiver

**IMPORTANT: BY ENTERING INTO THIS AGREEMENT, YOU WAIVE YOUR RIGHT TO PURSUE DISPUTES ON A CLASS, COLLECTIVE, OR REPRESENTATIVE BASIS.**

You agree that:
- Any dispute resolution proceedings (whether in arbitration or court) will be conducted **only on an individual basis** and not in a class, consolidated, or representative action
- You **waive any right** to participate in a class action lawsuit or class-wide arbitration against Maple
- You **may not** act as a class representative or participate as a member of a class in any purported class action
- Claims of two or more persons or entities **may not be arbitrated or litigated jointly** or consolidated unless Maple expressly consents

If this class action waiver is found to be invalid or unenforceable, then the arbitration provision in Section 14.3 shall be null and void (but all other provisions of this Agreement remain in effect).

### 14.6 Limitation on Time to Bring Claims

You agree that any claim or cause of action arising out of or related to this Agreement or the Services must be filed within **one (1) year** after the claim or cause of action arose; otherwise, such claim or cause of action is permanently barred.

### 14.7 Exceptions for Regulatory and Law Enforcement

Nothing in this Agreement prevents either party from:
- Reporting suspected illegal activity to law enforcement or regulatory authorities
- Cooperating with investigations by government agencies or Payment Networks
- Responding to lawful subpoenas, court orders, or regulatory requests
- Complying with applicable laws and regulations regarding reporting and disclosure

---

## 15. GENERAL PROVISIONS

### 15.1 Amendments and Modifications

Maple reserves the right to modify, amend, or update these Terms and Conditions at any time. We will provide notice of material changes by:
- Sending an email to the primary email address on file for your account
- Posting a notice in your merchant dashboard
- Posting the updated terms on our website at www.maplepayments.com/terms

**Notice Period:** Material changes will be effective **thirty (30) days** after notice is provided, unless a longer notice period is required by law.

**Acceptance:** Your continued use of the Services after the effective date of the modified terms constitutes your acceptance of the changes. If you do not agree to the modified terms, you must discontinue use of the Services and terminate this Agreement in accordance with Section 13.

**Enterprise Exception:** For Enterprise tier customers, material changes that reduce service levels, increase fees (beyond agreed-upon CPI adjustments), or otherwise materially reduce the rights or benefits provided to Customer will not apply until the end of the then-current contract term, unless Customer consents to the changes in writing.

### 15.2 Assignment

**By Merchant:** You may not assign or transfer this Agreement or any rights or obligations hereunder without Maple's prior written consent, which may be withheld in Maple's sole discretion. Any attempted assignment without consent is void.

**By Maple:** Maple may assign or transfer this Agreement, in whole or in part, without your consent to:
- Any affiliate or subsidiary of Maple
- Any successor in connection with a merger, acquisition, corporate reorganization, or sale of all or substantially all of Maple's assets or business related to the Services

**Effect of Assignment:** Any permitted assignment shall bind and inure to the benefit of the parties' respective successors and assigns.

### 15.3 Severability

If any provision of this Agreement is found by a court or arbitrator to be invalid, illegal, or unenforceable, the remaining provisions shall remain in full force and effect.

The invalid, illegal, or unenforceable provision shall be modified or reformed to the minimum extent necessary to make it valid, legal, and enforceable while preserving the parties' original intent. If such modification or reformation is not possible, the provision shall be severed from this Agreement.

### 15.4 Force Majeure

Neither party shall be liable for any failure or delay in performance of its obligations under this Agreement (other than payment obligations) due to circumstances beyond its reasonable control, including but not limited to:

- Natural disasters (earthquakes, floods, hurricanes, fires, severe weather)
- Acts of God
- Acts of war, terrorism, riots, or civil unrest
- Labor disputes, strikes, or lockouts (provided the affected party did not provoke the action)
- Governmental actions, laws, regulations, or orders
- Pandemics, epidemics, or public health emergencies
- Failures or disruptions of public utilities or communications networks
- Failures of third-party service providers (including Payment Networks, acquiring banks, or cloud infrastructure providers)
- Cyberattacks, denial-of-service attacks, or other security incidents not caused by the affected party's negligence

**Notice and Mitigation:** The affected party shall:
- Promptly notify the other party of the force majeure event
- Use reasonable efforts to mitigate the effects of the force majeure event
- Resume performance as soon as reasonably practicable

**Termination for Extended Force Majeure:** If a force majeure event prevents performance for more than sixty (60) consecutive days, either party may terminate this Agreement upon written notice without liability for such termination.

### 15.5 Entire Agreement

This Agreement, together with:
- The Order Form
- Schedule A (Service Level Agreement)
- Schedule B (Data Processing Addendum, if applicable)
- Any other schedules, exhibits, or amendments expressly incorporated by reference

constitutes the **entire agreement** between the parties regarding its subject matter and supersedes all prior agreements, understandings, negotiations, and discussions, whether written or oral, relating to such subject matter.

**Integration:** This Agreement integrates all of the terms and conditions relating to the Services. There are no oral or written agreements, representations, or warranties between the parties except as expressly set forth in this Agreement.

**Precedence:** In the event of a conflict between this Agreement and any exhibit or schedule, the terms of this Agreement shall control unless the exhibit or schedule expressly states that it supersedes a specific provision of this Agreement.

### 15.6 Notices

All notices, requests, consents, claims, demands, waivers, and other communications under this Agreement ("Notices") must be **in writing** and will be deemed to have been given:

**(a) When delivered by hand:** Upon personal delivery to the party to be notified

**(b) When sent by email:** 24 hours after transmission if no delivery failure notification is received

**(c) When sent by registered or certified mail:** Three (3) business days after deposit with a recognized international courier or national postal service with postage prepaid and return receipt requested

**(d) When sent by recognized courier:** Upon delivery as confirmed by the courier service

**Notice Addresses:**

Notices to Maple shall be sent to:
- **Email:** legal@maplepayments.com
- **Attention:** Legal Department
- **Address:** [Maple's registered office address]

Notices to Merchant shall be sent to:
- The primary email address associated with your merchant account
- The physical address provided during registration (for notices by mail or courier)

**Change of Address:** Each party shall promptly notify the other party of any change to its address for notices.

### 15.7 No Waiver

No failure or delay by either party in exercising any right, power, or privilege under this Agreement will operate as a waiver of such right, power, or privilege. No single or partial exercise of any right, power, or privilege will preclude any other or further exercise of such right, power, or privilege or the exercise of any other right, power, or privilege.

Any waiver must be in writing and signed by the party granting the waiver. A waiver of any breach of any provision of this Agreement will not be construed as a continuing waiver of other breaches of the same or other provisions.

### 15.8 Independent Contractors

The parties are **independent contractors**. This Agreement does not create a partnership, joint venture, agency, employment, or franchisor-franchisee relationship between the parties.

Neither party has the authority to:
- Bind or obligate the other party
- Make representations or warranties on behalf of the other party
- Incur liabilities or expenses on behalf of the other party
- Act as agent for the other party

Each party is solely responsible for:
- Its own employees, contractors, and agents
- Payment of all employment taxes and benefits
- Compliance with employment and labor laws
- Workers' compensation insurance

### 15.9 No Third-Party Beneficiaries

This Agreement is for the sole benefit of the parties and their respective successors and permitted assigns. This Agreement does not create any third-party beneficiary rights in any person or entity except as expressly stated herein (such as indemnified parties under Section 12.6).

### 15.10 Language

This Agreement is executed in the **English language**. Any translation is provided for convenience only. In case of any conflict, ambiguity, or inconsistency between the English version and any translation, the **English version shall prevail** and control.

### 15.11 Counterparts and Electronic Signatures

This Agreement may be executed in counterparts, each of which shall be deemed an original and all of which together shall constitute one and the same instrument.

Electronic signatures (including DocuSign, Adobe Sign, or similar electronic signature services) shall have the same force and effect as original signatures. Delivery of an executed signature page by email (PDF) or electronic signature service shall be as effective as delivery of a manually executed original.

---

## ACCEPTANCE AND SIGNATURES

By clicking "I Accept," signing below, or by accessing or using Maple's Services, you acknowledge that you have read, understood, and agree to be bound by this Master Services Agreement and all incorporated schedules and exhibits.

---

**MAPLE PAYMENTS INTERNATIONAL LIMITED**

By: ________________________________  
Name: [Authorized Signatory]  
Title: [Title]  
Date: _____________________________

---

**[CUSTOMER LEGAL NAME]**

By: ________________________________  
Name: [Authorized Signatory]  
Title: [Title]  
Date: _____________________________

---

## Contact Information

**Maple Payments International Limited**

- **Legal Inquiries:** legal@maplepayments.com
- **Customer Support:** support@maplepayments.com
- **Website:** www.maplepayments.com

---

# SCHEDULE A: SERVICE LEVEL AGREEMENT (SLA)

[Complete SLA from the earlier generated SLA with Starter/Growth/Enterprise tiers would be attached here]

---

# SCHEDULE B: DATA PROCESSING ADDENDUM (DPA)

[Standard GDPR/CCPA compliant DPA covering data processing terms, subprocessors, security measures, data subject rights, breach notification, international transfers, etc. would be attached here]

---

**END OF AGREEMENT**
