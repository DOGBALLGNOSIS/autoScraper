import os
from autoScraper import scrape_and_process_service, OUTPUT_DIR

def main():
    """
    Example test script showing how to use the scraping_microservice in practice.
    """

    urls_to_scrape = [
        "https://garethstarratt.framer.media/aws-project",
        "https://garethstarratt.framer.media/ml-project",
        "https://garethstarratt.framer.media/db-project",
        "https://garethstarratt.framer.media/re-visualizer-project",
        "https://garethstarratt.framer.media/ma-uav-project"
    ]

    # General-purpose instructions for the LLM
    user_instructions = """
    Extract all relevant project listings from the provided web page content.
    For each project, provide the following details:
    - Project Name
    - Languages Used
    - Project Description
    - Project Link (if available, otherwise N/A)

    Structure the extracted data in a JSON array where each product is an individual JSON object
    with the above fields. Ensure that the JSON is valid and properly formatted.
    """

    # Example required fields (ensures each JSON object has these keys)
    required_fields = ["Project Name", "Languages Used", "Project Description", "Project Link"]

    # Optional: List of standardized product names/categories (Applicable if you are gathering listing across multiple platforms/etc)
    standard_product_names = []

    # Call the microservice's scraping function
    api_key = os.getenv("OPENAI_API_KEY", "") 
    if not api_key:
        print("OpenAI API Key not set. Please set the OPENAI_API_KEY environment variable.")
        return

    output_file = scrape_and_process_service(
        urls=urls_to_scrape,
        instructions=user_instructions,
        openai_api_key=api_key,
        headless=True,
        standard_product_names=standard_product_names,
        required_fields=required_fields
    )

    if output_file:
        print("Extraction complete. Results:")
        print(f" - {output_file}")
    else:
        print("No data extracted. Check logs.")

if __name__ == "__main__":
    main()
