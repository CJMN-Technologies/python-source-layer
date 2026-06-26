import os
import sys
from dotenv import load_dotenv

# Ensure we can import from scraper
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from keywords import classify_post
from llm_classifier import classify_post_llm

load_dotenv()

def test_pipeline():
    print("--- SANDBOX PIPELINE LOGIC TEST ---")
    
    # Simulated post without emojis
    test_post_text = """
    PUPians, here is the official Academic Calendar for the upcoming Academic Year 2026-2027!
    
    Take note of the important dates for enrollment, start of classes, midterm examinations, and graduation. 
    Make sure to save this post for your reference!
    
    #PUPSKM #AcademicCalendar #PUP
    """
    
    print("\n1. Simulated Post Text:")
    print("-" * 40)
    print(test_post_text.strip())
    print("-" * 40)
    
    print("\n2. Testing Pre-Filter (keywords.py)")
    pre_category = classify_post(test_post_text)
    print(f"Result: {pre_category}")
    
    if pre_category == "academic":
        print("✅ SUCCESS: The post passed the pre-filter and was categorized as 'academic'!")
    else:
        print("❌ FAILED: The pre-filter blocked or miscategorized the post.")
        return
        
    print("\n3. Testing LLM Classifier (llm_classifier.py) with Gemini")
    llm_res = classify_post_llm(test_post_text)
    
    print(f"Result: {llm_res}")
    
    if llm_res and llm_res.get("category") == "academic_calendar":
        print("✅ SUCCESS: Gemini correctly analyzed the text and classified it as an 'academic_calendar'!")
        print("This proves that the pipeline will now successfully capture and save the PUP calendars.")
    else:
        print("❌ FAILED: Gemini did not classify it as an academic calendar.")

if __name__ == "__main__":
    test_pipeline()
