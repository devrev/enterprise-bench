# Master Services Agreement - Standard Template
## Maple Software Payment Platform Services

**Document Version:** 2.1  
**Effective Date:** January 1, 2024  
**Document Type:** Standard Service Level Agreement

---

## 1. Service Level Agreement (SLA) Terms

This Service Level Agreement defines the performance standards and support commitments Maple Software ("Maple") provides to Customer under the Master Services Agreement.

---

## 2. System Availability & Uptime

### 2.1 Platform Uptime Guarantee
**Commitment:** 99.9% monthly uptime  
**Measurement Period:** Calendar month  
**Calculation Method:** (Total Minutes in Month - Downtime Minutes) / Total Minutes in Month × 100

**Exclusions from Downtime:**
- Scheduled maintenance windows (with 72-hour notice)
- Customer-caused outages
- Force majeure events
- Third-party service provider failures

**Uptime Credits:**
- 99.5% - 99.8% uptime: 10% monthly service credit
- 99.0% - 99.49% uptime: 25% monthly service credit
- Below 99.0% uptime: 50% monthly service credit

---

## 3. Support Response Times

### 3.1 Priority Definitions

**P0 - Critical (Service Down)**
- Complete platform unavailability
- Payment processing fully blocked
- Data breach or security incident
- Affects all or majority of transactions

**P1 - High (Major Impact)**
- Significant feature degradation
- Payment processing severely impaired
- Affects substantial portion of transactions
- Major API failures

**P2 - Medium (Moderate Impact)**
- Partial feature degradation
- Workaround available
- Isolated transaction issues
- Non-critical API issues

**P3 - Low (Minor Impact)**
- Cosmetic issues
- Feature requests
- General questions
- Documentation requests

### 3.2 Response & Resolution Targets

| Priority | Initial Response Time | Resolution Target | Business Hours |
|----------|----------------------|-------------------|----------------|
| **P0** | 15 minutes | 4 hours | 24/7 |
| **P1** | 1 hour | 24 hours | 24/7 |
| **P2** | 4 hours | 3 business days | Business hours only |
| **P3** | 8 hours | 5 business days | Business hours only |

**Business Hours Definition:** Monday - Friday, 9:00 AM - 6:00 PM in Customer's primary business timezone, excluding recognized holidays.

**Initial Response Definition:** First substantive communication from Maple support acknowledging the issue and providing initial assessment or request for information.

**Resolution Definition:** Issue is resolved, workaround provided, or permanent fix deployed to production.

---

## 4. Billing & Financial Operations

### 4.1 Billing Dispute Resolution

**Investigation Timeline:** 5 business days from receipt of dispute  
**Resolution Timeline:** 10 business days from receipt of dispute

**Process:**
1. Customer submits billing dispute via support portal
2. Maple acknowledges within 1 business day
3. Investigation completed within 5 business days
4. Resolution (adjustment, credit, or explanation) within 10 business days

**Escalation:** Disputes unresolved after 10 business days automatically escalate to Account Manager and Finance Director.

### 4.2 Subscription Changes

**Processing Timeline:** 2 business days

**Covered Changes:**
- Plan upgrades/downgrades
- User seat additions/removals
- Feature module additions/removals
- Payment method updates

**Effective Date:** Changes take effect within 2 business days of request submission, or on Customer-specified future date.

---

## 5. API Performance

### 5.1 API Availability
**Commitment:** 99.5% monthly availability  
**Measurement:** Successful API responses / Total API requests × 100

### 5.2 API Latency
**Commitment:** 95th percentile (p95) response time < 500ms  
**Measurement:** Monthly aggregate across all API endpoints

**Performance Credits:**
- 99.0% - 99.49% availability: 5% monthly service credit
- Below 99.0% availability: 15% monthly service credit
- p95 latency 500ms - 1000ms: 5% monthly service credit
- p95 latency > 1000ms: 10% monthly service credit

---

## 6. Reporting & Data Availability

### 6.1 Monthly Financial Reports
**Delivery Timeline:** Within 3 business days of month-end

**Included Reports:**
- Transaction summary
- Settlement reconciliation
- Fee breakdown
- Chargeback summary

### 6.2 Data Export Requests
**Standard Exports:** Within 2 business days  
**Custom Exports:** Within 5 business days

---

## 7. SLA Measurement & Reporting

### 7.1 SLA Compliance Tracking
Maple will provide monthly SLA compliance reports including:
- Uptime percentage and any downtime incidents
- Support ticket response/resolution metrics by priority
- API performance metrics
- Billing operations metrics

### 7.2 Business Days Calculation
**Business Days** exclude:
- Saturdays and Sundays
- US Federal Holidays:
  - New Year's Day
  - Martin Luther King Jr. Day
  - Presidents' Day
  - Memorial Day
  - Independence Day
  - Labor Day
  - Thanksgiving Day
  - Day after Thanksgiving
  - Christmas Day

**Timezone:** All SLA timelines measured in Customer's primary business timezone unless otherwise specified.

---

## 8. SLA Credits & Remedies

### 8.1 Credit Request Process
Customer must submit SLA credit requests within 30 days of the relevant month-end. Credits applied to following month's invoice.

### 8.2 Exclusive Remedy
SLA credits represent Customer's sole and exclusive remedy for Maple's failure to meet SLA commitments, up to maximum of 50% monthly service fees.

---

## 9. Escalation Procedures

### 9.1 Support Escalation Path
1. **Level 1:** Support Engineer (initial contact)
2. **Level 2:** Senior Support Engineer (>24 hours unresolved)
3. **Level 3:** Support Manager (>48 hours unresolved)
4. **Level 4:** Director of Customer Success (critical issues or >72 hours)

### 9.2 Executive Escalation
For critical business impact, Customer may escalate directly to:
- **VP of Customer Operations:** escalations@maple.com
- **Account Manager** (if applicable to Customer's tier)

---

## 10. SLA Exceptions & Modifications

### 10.1 Planned Maintenance
Maple will provide 72-hour advance notice for planned maintenance. Maintenance windows excluded from uptime calculations.

### 10.2 SLA Modifications
SLA terms may be modified with 90-day written notice to Customer. Customer may terminate agreement within 30 days of notification without penalty.

---

**Document Control:**
- **Version:** 2.1
- **Last Updated:** January 1, 2024
- **Next Review:** January 1, 2025
- **Owner:** Maple Software Legal & Customer Operations

---

## Appendix A: Priority Classification Examples

**P0 Examples:**
- Complete payment processing outage
- API returning 500 errors for all requests
- Database unavailability
- Security breach

**P1 Examples:**
- Payment processing success rate drops below 95%
- Specific payment method completely unavailable
- Dashboard login failures affecting >25% of users
- Critical API endpoint failing

**P2 Examples:**
- Report generation delays
- Minor feature bugs with workarounds
- Performance degradation not affecting core transactions
- Single user access issues

**P3 Examples:**
- Documentation clarifications
- Feature enhancement requests
- UI/UX improvement suggestions
- General how-to questions
