import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="DCF Valuation Model", layout="centered")

# --- 1. DATA GATHERING FUNCTION (Cached for speed) ---
@st.cache_data
def get_dcf_inputs(ticker):
    stock = yf.Ticker(ticker)
    
    # Check if data exists
    if stock.info.get('marketCap') is None:
        return None
    
    # Basic info
    market_cap = stock.info.get('marketCap')
    shares_outstanding = stock.info.get('sharesOutstanding')
    current_price = stock.info.get('currentPrice')
    
    # Quarterly Financials
    q_cashflow = stock.quarterly_cash_flow
    q_financials = stock.quarterly_financials
    
    # TTM Calculations
    ttm_revenue = q_financials.loc['Total Revenue'].iloc[:4].sum()
    op_cash = q_cashflow.loc['Operating Cash Flow'].iloc[:4].sum()
    
    # CapEx handling (Yahoo labels vary)
    if 'Capital Expenditure' in q_cashflow.index:
        cap_ex = q_cashflow.loc['Capital Expenditure'].iloc[:4].sum()
    elif 'Capex' in q_cashflow.index:
        cap_ex = q_cashflow.loc['Capex'].iloc[:4].sum()
    else:
        cap_ex = 0
            
    ttm_fcf = op_cash + cap_ex 
    
    # WACC Inputs
    beta = stock.info.get('beta', 1.0)
    
    # Risk Free Rate
    try:
        treasury = yf.Ticker("^TNX") 
        risk_free_rate = treasury.history(period="1d")['Close'].iloc[-1] / 100
    except:
        risk_free_rate = 0.04 

    market_return = 0.10
    cost_of_equity = risk_free_rate + beta * (market_return - risk_free_rate)
    
    # Cost of Debt
    try:
        ttm_ebit = q_financials.loc['EBIT'].iloc[:4].sum()
        ttm_pretax = q_financials.loc['Pretax Income'].iloc[:4].sum()
        implied_interest = abs(ttm_ebit - ttm_pretax)
    except KeyError:
        implied_interest = 0.0

    total_debt = stock.info.get('totalDebt', 0)
    cost_of_debt = (implied_interest / total_debt) if total_debt > 0 else 0.0
    
    # Tax Rate
    try:
        tax_prov = q_financials.loc['Tax Provision'].iloc[:4].sum()
        tax_rate = tax_prov / ttm_pretax if ttm_pretax != 0 else 0.21
    except:
        tax_rate = 0.21
    
    # WACC Calc
    total_val = market_cap + total_debt
    equity_w = market_cap / total_val
    debt_w = total_debt / total_val
    wacc = (equity_w * cost_of_equity) + (debt_w * cost_of_debt * (1 - tax_rate))
    
    return {
        "Ticker": ticker.upper(),
        "Current Price": current_price,
        "Shares Outstanding": shares_outstanding,
        "TTM Revenue": ttm_revenue,
        "TTM FCF": ttm_fcf,
        "WACC": wacc,
        "Total Debt": total_debt,
        "Cash": stock.info.get('totalCash', 0)
    }

# --- 2. APP UI LAYOUT ---
st.title("📊 DCF Valuation Tool")
st.markdown("Enter a ticker and adjust assumptions to calculate fair value.")

# Sidebar for Inputs
with st.sidebar:
    st.header("Model Inputs")
    ticker_input = st.text_input("Stock Ticker", value="AAPL")
    
    st.divider()
    
    # --- Growth Rates ---
    st.subheader("Growth Assumptions")
    growth_rate_percent = st.slider(
        "Revenue Growth Rate (Years 1-5)", 
        min_value=-10.0, max_value=50.0, value=5.0, step=0.5, format="%.1f%%"
    )
    growth_rate = growth_rate_percent / 100.0
    
    terminal_growth_percent = st.slider(
        "Terminal Growth Rate (Year 5+)", 
        min_value=0.1, max_value=5.0, value=2.5, step=0.1, format="%.1f%%"
    )
    terminal_growth_rate = terminal_growth_percent / 100.0

    st.divider()

    # --- FCF Margin Override ---
    st.subheader("Margin Assumptions")
    use_manual_margin = st.checkbox("Override Historical FCF Margin?")
    
    if use_manual_margin:
        manual_margin_percent = st.slider(
            "Projected FCF Margin", 
            min_value=-10.0, max_value=50.0, value=20.0, step=0.5, format="%.1f%%"
        )
        projected_margin = manual_margin_percent / 100.0
    else:
        projected_margin = None # Will be calculated from data

    st.info("Note: Terminal Growth must be lower than WACC.")

# --- 3. MAIN LOGIC ---
if ticker_input:
    with st.spinner(f"Fetching data for {ticker_input}..."):
        try:
            data = get_dcf_inputs(ticker_input)
            
            if data is None:
                st.error("Could not fetch data. Please check the ticker.")
            else:
                # --- CALCULATIONS ---
                revenue = data['TTM Revenue']
                fcf = data['TTM FCF']
                wacc = data['WACC']
                shares = data['Shares Outstanding']
                cash = data['Cash']
                debt = data['Total Debt']
                
                # Calculate Historical Margin
                historical_margin = fcf / revenue
                
                # Determine which margin to use for projections
                if projected_margin is None:
                    final_margin_used = historical_margin
                    margin_label = "Historical (TTM)"
                else:
                    final_margin_used = projected_margin
                    margin_label = "Manual Projection"

                # Show Key Stats
                col1, col2, col3 = st.columns(3)
                col1.metric("Current Price", f"${data['Current Price']:.2f}")
                col2.metric("WACC", f"{wacc:.2%}")
                col3.metric(f"FCF Margin ({margin_label})", f"{final_margin_used:.1%}")

                # Projections
                future_fcf = []
                discount_factors = []
                discounted_fcf = []
                years = range(1, 6)
                
                projection_data = []

                current_rev = revenue
                for year in years:
                    current_rev = current_rev * (1 + growth_rate)
                    estimated_fcf = current_rev * final_margin_used
                    disc_factor = 1 / ((1 + wacc) ** year)
                    pv_fcf = estimated_fcf * disc_factor
                    
                    future_fcf.append(estimated_fcf)
                    discount_factors.append(disc_factor)
                    discounted_fcf.append(pv_fcf)
                    
                    projection_data.append({
                        "Year": year,
                        "Revenue ($B)": current_rev/1e9,
                        "FCF ($B)": estimated_fcf/1e9,
                        "PV of FCF ($B)": pv_fcf/1e9
                    })

                # Display Projection Table
                st.subheader("5-Year Projections")
                df_proj = pd.DataFrame(projection_data)
                st.dataframe(df_proj.style.format("{:.2f}"))

                # Terminal Value
                fcf_year_5 = future_fcf[-1]
                if wacc <= terminal_growth_rate:
                    st.error("Error: WACC must be higher than Terminal Growth Rate.")
                    st.stop()
                    
                terminal_value = (fcf_year_5 * (1 + terminal_growth_rate)) / (wacc - terminal_growth_rate)
                pv_terminal_value = terminal_value * discount_factors[-1]

                # Final Value Steps
                sum_pv_fcf = sum(discounted_fcf)
                enterprise_value = sum_pv_fcf + pv_terminal_value
                equity_value = enterprise_value + cash - debt
                calculated_share_price = equity_value / shares
                
                actual_price = data['Current Price']
                difference = 1 - (calculated_share_price / actual_price)

                # --- RESULTS DISPLAY ---
                st.divider()
                st.subheader("Valuation Results")
                
                res_col1, res_col2 = st.columns(2)
                
                with res_col1:
                    st.write(f"**Enterprise Value:** ${enterprise_value/1e9:,.2f}B")
                    st.write(f"**Equity Value:** ${equity_value/1e9:,.2f}B")
                    st.write(f"**PV of Terminal Value:** ${pv_terminal_value/1e9:,.2f}B")

                with res_col2:
                    st.metric("Fair Value (Calculated)", f"${calculated_share_price:.2f}")
                    
                    if calculated_share_price > actual_price:
                        st.success(f"UNDERVALUED by {abs(difference):.1%}")
                    else:
                        st.error(f"OVERVALUED by {abs(difference):.1%}")

        except Exception as e:
            st.error(f"An error occurred: {e}")