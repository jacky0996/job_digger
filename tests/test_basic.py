def test_environment_variable_loading():
    """
    測試環境變數是否能被正確讀取 (模擬單元測試範例)
    """
    import os

    from dotenv import load_dotenv

    load_dotenv()
    headless = os.getenv("BROWSER_HEADLESS")
    assert headless is not None


def test_data_cleaning_logic():
    """
    這是一個模擬資料清洗 (Stage B) 的單元測試
    """
    raw_salary = "月薪40,000~60,000元"
    # 假設這是一個清洗函式
    clean_salary = raw_salary.replace("元", "").replace(",", "")
    assert "40000" in clean_salary
    assert "60000" in clean_salary
