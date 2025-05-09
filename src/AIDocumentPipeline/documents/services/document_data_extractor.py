from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from pdf2image import convert_from_bytes
import base64
from openai import AzureOpenAI
import io
from typing import TypeVar, Optional
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeResult, DocumentContentFormat
from shared.confidence.confidence_utils import merge_confidence_values
from shared.confidence.openai_confidence import evaluate_confidence as evaluate_confidence_openai
from shared.confidence.document_intelligence_confidence import evaluate_confidence as evaluate_confidence_di
from shared.confidence.confidence_result import ConfidenceResult, OVERALL_CONFIDENCE_KEY

ResponseFormatT = TypeVar(
    "ResponseFormatT"
)

ExtractionConfidenceResult = ConfidenceResult[ResponseFormatT | None]


class DocumentDataExtractorOptions:
    """Defines the configuration options for extracting data from a document using Azure OpenAI."""

    def __init__(self, extraction_prompt: str, page_start: Optional[int], page_end: Optional[int], aiservices_endpoint: Optional[str], openai_endpoint: str, deployment_name: str, max_tokens: int = 4096, temperature: float = 0.1, top_p: float = 0.1):
        """Initializes a new instance of the DocumentDataExtractorOptions class.

        :param extraction_prompt: The prompt to use for extracting data from the document, including the expected output format.
        :param page_start: The starting page number of the document to extract data from.
        :param page_end: The ending page number of the document to extract data from.
        :param endpoint: The Azure OpenAI endpoint to use for the request.
        :param deployment_name: The name of the model deployment to use for the request.
        :param max_tokens: The maximum number of tokens to generate in the response. Default is 4096.
        :param temperature: The sampling temperature for the model. Default is 0.1.
        :param top_p: The nucleus sampling parameter for the model. Default is 0.1.
        """

        self.system_prompt = f"""You are an AI assistant that extracts data from documents."""
        self.extraction_prompt = extraction_prompt
        self.page_start = page_start
        self.page_end = page_end
        self.openai_endpoint = openai_endpoint
        self.aiservices_endpoint = aiservices_endpoint
        self.deployment_name = deployment_name
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.top_p = top_p


class DocumentDataExtractor:
    """Defines a class for extracting structured data from a document using Azure OpenAI GPT models that support image inputs."""

    def __init__(self, credential: DefaultAzureCredential):
        """Initializes a new instance of the DocumentDataExtractor class.

        :param credential: The Azure credential to use for authenticating with the Azure OpenAI service.
        """

        self.credential = credential

    def from_bytes(self, document_bytes: bytes, response_format: type[ResponseFormatT], options: DocumentDataExtractorOptions) -> ExtractionConfidenceResult:
        """Extracts structured data from the specified document bytes by converting the document to images and using an Azure OpenAI model to extract the data.

        :param document_bytes: The byte array content of the document to extract data from.
        :param options: The options for configuring the Azure OpenAI request for extracting data.
        :return: The structured data extracted from the document as a dictionary.
        """

        client = self.__get_openai_client__(options)
        di_client = self.__get_document_intelligence_client__(options)

        if options.page_start and options.page_end:
            page_range = f"{options.page_start}-{options.page_end}"
        else:
            page_range = None

        # For a more accurate extraction, we can use the Document Intelligence service to extract the document layout and convert it to markdown.
        if di_client:
            poller = di_client.begin_analyze_document(
                model_id="prebuilt-layout",
                body=document_bytes,
                pages=page_range,
                output_content_format=DocumentContentFormat.MARKDOWN,
                content_type="application/pdf"
            )
            result: AnalyzeResult = poller.result()
            document_markdown = result.content
        else:
            document_markdown = None

        image_uris = self.__get_document_image_uris__(
            document_bytes, options.page_start, options.page_end)

        user_content = []
        user_content.append({
            "type": "text",
            "text": options.extraction_prompt
        })

        if document_markdown:
            user_content.append({
                "type": "text",
                "text": document_markdown
            })

        for image_uri in image_uris:
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": image_uri
                }
            })

        completion = client.beta.chat.completions.parse(
            model=options.deployment_name,
            messages=[
                {
                    "role": "system",
                    "content": options.system_prompt,
                },
                {
                    "role": "user",
                    "content": user_content
                }
            ],
            response_format=response_format,
            max_tokens=4096,
            temperature=0.1,
            top_p=0.1,
            # Enabled to determine the confidence of the response.
            logprobs=True
        )

        response_obj = completion.choices[0].message.parsed
        response_obj_dict = response_obj.model_dump()

        confidence_openai = evaluate_confidence_openai(
            extract_result=response_obj_dict,
            choice=completion.choices[0]
        )

        if di_client:
            confidence_di = evaluate_confidence_di(
                extract_result=response_obj_dict,
                analyze_result=result
            )
            confidence = merge_confidence_values(
                confidence_a=confidence_di,
                confidence_b=confidence_openai
            )
        else:
            confidence = confidence_openai

        return ExtractionConfidenceResult(
            data=response_obj,
            confidence_scores=confidence,
            overall_confidence=confidence[OVERALL_CONFIDENCE_KEY]
        )

    def __get_openai_client__(self, options: DocumentDataExtractorOptions) -> AzureOpenAI:
        token_provider = get_bearer_token_provider(
            self.credential, "https://cognitiveservices.azure.com/.default")

        client = AzureOpenAI(
            api_version="2024-12-01-preview",
            azure_endpoint=options.openai_endpoint,
            azure_ad_token_provider=token_provider)

        return client

    def __get_document_intelligence_client__(self, options: DocumentDataExtractorOptions) -> Optional[DocumentIntelligenceClient]:
        if not options.aiservices_endpoint:
            return None

        document_intelligence_client = DocumentIntelligenceClient(
            endpoint=options.aiservices_endpoint,
            credential=self.credential
        )

        return document_intelligence_client

    def __get_document_image_uris__(self, document_bytes: bytes, page_start: Optional[int], page_end: Optional[int]) -> list:
        """Converts the specified document bytes to images using the pdf2image library and returns the image URIs.

        To call this method, poppler-utils must be installed on the system.
        """

        pages = convert_from_bytes(document_bytes)

        image_uris = []

        if page_start and page_end:
            pages = pages[page_start-1:page_end]

        for page in pages:
            byteIO = io.BytesIO()
            page.save(byteIO, format='PNG')
            base64_data = base64.b64encode(byteIO.getvalue()).decode('utf-8')
            image_uris.append(f"data:image/png;base64,{base64_data}")

        return image_uris
