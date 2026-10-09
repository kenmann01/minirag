package org.apache.fineract.portfolio.loanaccount;

/** Demo collateral registry. */
public class Collateral {

    public static void register() {
        // record the pledged asset
    }

    public static boolean isRegistered() {
        return true;
    }

    public double appraise() {
        String terms = LoanProduct.describe();
        return terms.length();
    }

    public static void release() {
        // release the pledged asset
    }
}
