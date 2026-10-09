package org.apache.fineract.portfolio.loanaccount;

/** Demo loan application flow. */
public class LoanApplication {

    public void submit() {
        this.validate();
        Client.verifyIdentity();
    }

    public boolean validate() {
        return Collateral.isRegistered();
    }

    public void withdraw() {
        Client.notifyWithdrawal();
    }

    public double assignScore() {
        String terms = LoanProduct.describe();
        return score(terms);
    }

    double score(String terms) {
        return terms.length();
    }
}
